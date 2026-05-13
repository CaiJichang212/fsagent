"""LangGraph assembly for the explicit fast/plan runtime."""

from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from threading import RLock
from typing import Any

from deepagents import FilesystemPermission, create_deep_agent
from deepagents._models import get_model_identifier, get_model_provider
from deepagents.backends.protocol import BackendFactory, BackendProtocol
from deepagents.profiles.harness import HarnessProfile, register_harness_profile
from deepagents.profiles.harness.harness_profiles import _HARNESS_PROFILES, _ensure_harness_profiles_loaded
from langchain.agents.middleware import HumanInTheLoopMiddleware, InterruptOnConfig
from langchain.agents.middleware.types import AgentMiddleware, ToolCallRequest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.store.base import BaseStore
from langgraph.types import Checkpointer, Command, interrupt

from fsagent.runtime.agent_observability import (
    AgentLogContext,
    AgentObservabilityMiddleware,
    emit_agent_event,
)
from fsagent.runtime.approval import build_plan_review_payload, normalize_review_command
from fsagent.runtime.context import build_context_bundle, format_context_for_planner
from fsagent.runtime.executor import execute_plan
from fsagent.runtime.fast import create_fast_agent, run_fast
from fsagent.runtime.formatter import format_final_report
from fsagent.runtime.mcp import load_runtime_mcp_tools, resolve_mcp_config_path, summarize_mcp_servers_for_review
from fsagent.runtime.planner import generate_plan
from fsagent.runtime.planner_agent import create_planner_agent
from fsagent.runtime.planner_capabilities import PlannerCapabilitySummaryBuilder
from fsagent.runtime.policy import ToolPolicyMiddleware, policy_interrupt_on_for_tools
from fsagent.runtime.prompts import (
    EXECUTOR_SYSTEM_PROMPT,
    PLANNER_SYSTEM_PROMPT,
)
from fsagent.runtime.reviews import build_deviation_review_payload, build_mcp_review_payload
from fsagent.runtime.state import RuntimeState
from fsagent.runtime.verifier import verify_execution_async

ProgressCallback = Callable[[Mapping[str, object]], Awaitable[None]]
_HARNESS_PROFILE_LOCK = RLock()


class ExecutorTodoToolGuardMiddleware(AgentMiddleware[RuntimeState, Any, Any]):
    """Convert unexpected executor `write_todos` calls into model-visible errors."""

    state_schema = RuntimeState

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage],
    ) -> ToolMessage:
        """Handle unexpected synchronous executor todo tool calls."""
        if _tool_call_name(request) == "write_todos":
            return _executor_write_todos_error(request)
        return handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage]],
    ) -> ToolMessage:
        """Handle unexpected asynchronous executor todo tool calls."""
        if _tool_call_name(request) == "write_todos":
            return _executor_write_todos_error(request)
        return await handler(request)


def create_runtime(  # noqa: C901, PLR0915
    model: str | BaseChatModel,
    tools: Sequence[BaseTool | Callable[..., Any] | dict[str, Any]] | None = None,
    *,
    system_prompt: str | SystemMessage | None = None,
    skills: list[str] | None = None,
    memory: list[str] | None = None,
    backend: BackendProtocol | BackendFactory | None = None,
    interrupt_on: dict[str, bool | InterruptOnConfig] | None = None,
    checkpointer: Checkpointer | None = None,
    store: BaseStore | None = None,
    mcp_config_path: str | None = None,
    no_mcp: bool = False,
    trust_project_mcp: bool | None = None,
    tool_policy_profile: str = "dev-default",
    permissions: list[FilesystemPermission] | None = None,
) -> CompiledStateGraph:
    """Create the explicit fast/plan runtime graph.

    Args:
        model: Chat model or model identifier.
        tools: Optional regular tools.
        system_prompt: Optional caller system prompt.
        skills: Skill sources passed to Deep Agents in plan mode.
        memory: Memory sources passed to Deep Agents in plan mode.
        backend: Deep Agents backend.
        interrupt_on: Tool-level HITL config.
        checkpointer: LangGraph checkpointer.
        store: LangGraph store.
        mcp_config_path: Optional MCP config path.
        no_mcp: Disable MCP loading.
        trust_project_mcp: Whether project-level stdio MCP servers are trusted.
        tool_policy_profile: fsagent tool policy profile.
        permissions: Deep Agents filesystem permissions for plan-mode executor.

    Returns:
        Compiled runtime graph.
    """
    resolved_mcp_config_path = resolve_mcp_config_path(mcp_config_path)
    planner_capability_summary = PlannerCapabilitySummaryBuilder(
        regular_tools=tools or [],
        skills=skills,
        memory=memory,
        backend=backend,
        mcp_config_path=resolved_mcp_config_path,
        no_mcp=no_mcp,
        trust_project_mcp=trust_project_mcp,
    ).build()
    planner_system_prompt = _planner_system_prompt(
        system_prompt=system_prompt,
        capability_summary=planner_capability_summary.text,
    )

    async def combined_tools() -> list[BaseTool | Callable[..., Any] | dict[str, Any]]:
        mcp = await load_runtime_mcp_tools(
            resolved_mcp_config_path,
            no_mcp=no_mcp,
            trust_project_mcp=trust_project_mcp,
        )
        return [*(tools or []), *mcp.tools]

    def mcp_review_gate(state: RuntimeState, *, next_node: str) -> Command[Any]:
        servers = summarize_mcp_servers_for_review(
            resolved_mcp_config_path,
            no_mcp=no_mcp,
            trust_project_mcp=trust_project_mcp,
        )
        has_high_risk_server = any(server.get("risk") == "high" for server in servers)
        if not has_high_risk_server or state.get("mcp_review_approved") is True:
            return Command(goto=next_node)

        command = _normalize_gate_command(interrupt(build_mcp_review_payload(servers=servers)))
        action = command.get("action")
        if action == "approve":
            return Command(update={"mcp_review_approved": True}, goto=next_node)
        if action == "deny":
            return Command(update={"mcp_review_approved": False, "status": "needs_revision"}, goto=END)
        return Command(update={"mcp_review_approved": False, "status": "cancelled"}, goto=END)

    def fast_mcp_review_gate(state: RuntimeState) -> Command[Any]:
        return mcp_review_gate(state, next_node="fast_runner")

    def plan_mcp_review_gate(state: RuntimeState) -> Command[Any]:
        return mcp_review_gate(state, next_node="executor")

    async def fast_runner(state: RuntimeState, config: RunnableConfig | None = None) -> dict[str, Any]:
        on_event = _progress_callback(config)
        fast_tools = await combined_tools()
        policy_interrupt_on = policy_interrupt_on_for_tools(
            fast_tools,
            profile=tool_policy_profile,
            phase="fast_runner",
            base=interrupt_on,
        )
        middleware = [
            ToolPolicyMiddleware(
                profile=tool_policy_profile,
                phase="fast_runner",
                on_event=on_event,
            ),
            AgentObservabilityMiddleware(
                context=AgentLogContext(agent_mode="fast", phase="fast_runner"),
                on_event=on_event,
            ),
        ]
        if policy_interrupt_on:
            middleware.append(HumanInTheLoopMiddleware(policy_interrupt_on))
        agent = create_fast_agent(
            model=model,
            tools=fast_tools,
            system_prompt=_system_prompt_text(system_prompt),
            middleware=middleware,
            checkpointer=checkpointer,
            store=store,
            on_event=on_event,
        )
        content = _last_user_content(state)
        final = await run_fast(agent=agent, content=content, config=config, on_event=on_event)
        return {"final_response": final}

    async def planner(state: RuntimeState, config: RunnableConfig | None = None) -> dict[str, Any]:
        await _emit_planner_capability_warnings(config, planner_capability_summary.warnings)
        await _emit_progress(config, {"kind": "planner.started", "message": "开始生成计划"})
        original_user_content = _last_user_content(state)
        context_bundle = build_context_bundle(
            user_message=original_user_content,
            repo_rules=_planner_repo_rules(system_prompt),
        )
        await _emit_planner_context_warnings(config, context_bundle.warnings)
        planner_content = format_context_for_planner(context_bundle)
        on_event = _progress_callback(config)
        planner_context = AgentLogContext(agent_mode="plan", phase="planner")
        agent = create_planner_agent(
            model=model,
            system_prompt=planner_system_prompt,
            on_event=on_event,
            checkpointer=checkpointer,
            store=store,
        )
        await emit_agent_event(
            on_event,
            planner_context,
            "agent.started",
            "计划生成 agent 启动",
        )
        generated = await generate_plan(
            agent=agent,
            content=planner_content,
            feedback=state.get("plan_feedback"),
            config=config,
        )
        await emit_agent_event(
            on_event,
            planner_context,
            "agent.completed",
            "计划生成 agent 完成(待审核)",
            todo_count=len(generated.todos),
        )
        await _emit_progress(
            config,
            {
                "kind": "planner.completed",
                "message": f"计划生成完成, 共 {len(generated.todos)} 项",
                "todos": generated.todos,
                "plan_meta": generated.plan_meta,
            },
        )
        return {"todos": generated.todos, "plan_meta": generated.plan_meta}

    def plan_review(state: RuntimeState) -> Command[Any]:
        payload = build_plan_review_payload(todos=state["todos"], plan_meta=state.get("plan_meta"))
        command = normalize_review_command(interrupt(payload))
        action = command["action"]
        if action == "approve":
            return Command(goto="plan_mcp_review")
        if action == "edit":
            return Command(
                update={
                    "todos": command["todos"],
                    "plan_meta": command.get("plan_meta", state.get("plan_meta")),
                },
                goto="plan_review",
            )
        if action == "retry":
            return Command(
                update={
                    "plan_review_count": state.get("plan_review_count", 0) + 1,
                    "plan_feedback": command.get("feedback"),
                },
                goto="planner",
            )
        return Command(update={"final_response": None}, goto=END)

    async def executor(state: RuntimeState, config: RunnableConfig | None = None) -> dict[str, Any]:
        on_event = _progress_callback(config)
        executor_tools = await combined_tools()
        policy_interrupt_on = policy_interrupt_on_for_tools(
            executor_tools,
            profile=tool_policy_profile,
            phase="executor",
            base=interrupt_on,
        )
        middleware = [
            ExecutorTodoToolGuardMiddleware(),
            ToolPolicyMiddleware(
                profile=tool_policy_profile,
                phase="executor",
                on_event=on_event,
            ),
            AgentObservabilityMiddleware(
                context=AgentLogContext(agent_mode="plan", phase="executor"),
                on_event=on_event,
            ),
        ]
        with _executor_todo_middleware_excluded(model):
            agent = create_deep_agent(
                model=model,
                tools=executor_tools,
                system_prompt=_join_prompts(system_prompt, EXECUTOR_SYSTEM_PROMPT),
                middleware=middleware,
                skills=skills,
                memory=memory,
                backend=backend,
                interrupt_on=policy_interrupt_on or None,
                checkpointer=checkpointer,
                store=store,
                permissions=permissions,
            )
        await emit_agent_event(
            on_event,
            AgentLogContext(agent_mode="plan", phase="executor"),
            "agent.started",
            "Executor agent 启动",
            todo_count=len(state["todos"]),
        )
        result = await execute_plan(
            agent=agent,
            todos=state["todos"],
            plan_meta=state.get("plan_meta"),
            config=config,
            on_event=_progress_callback(config),
        )
        await emit_agent_event(
            on_event,
            AgentLogContext(agent_mode="plan", phase="executor"),
            "agent.completed",
            "Executor agent 完成",
            todo_count=len(result.todos),
            execution_log_count=len(result.execution_log),
        )
        return {
            "todos": result.todos,
            "execution_log": result.execution_log,
            "evidence": result.evidence,
            "final_response": result.final_result,
            "deviation_requested": result.deviation_requested,
            "deviation_reason": result.deviation_reason,
        }

    def deviation_review_gate(state: RuntimeState) -> Command[Any]:
        if not state.get("deviation_requested"):
            return Command(goto="verifier")

        reason = state.get("deviation_reason") or "Executor requested a deviation from the approved plan."
        payload = build_deviation_review_payload(
            subject="Executor requested plan deviation",
            proposed_input_summary=reason,
        )
        command = _normalize_gate_command(interrupt(payload))
        action = command.get("action")
        if action == "approve":
            return Command(update={"deviation_requested": False}, goto="verifier")
        if action == "replan":
            return Command(update={"status": "needs_revision"}, goto=END)
        return Command(update={"status": "cancelled"}, goto=END)

    async def verifier(state: RuntimeState) -> dict[str, Any]:
        result = await verify_execution_async(
            todos=state["todos"],
            execution_log=state.get("execution_log", []),
            plan_meta=state.get("plan_meta"),
            verification=state.get("verification"),
        )
        return {"verification": result.verification, "status": result.status}

    def formatter(state: RuntimeState) -> dict[str, Any]:
        report = format_final_report(
            result=state.get("final_response") or "Plan execution did not produce a final result.",
            todos=state["todos"],
            execution_log=state.get("execution_log", []),
            artifacts=state.get("artifacts", []),
            evidence=state.get("evidence", []),
            verification=state.get("verification", []),
        )
        return {"final_response": report}

    graph = StateGraph(RuntimeState)
    graph.add_node("fast_mcp_review", fast_mcp_review_gate)
    graph.add_node("fast_runner", fast_runner)
    graph.add_node("planner", planner)
    graph.add_node("plan_review", plan_review)
    graph.add_node("plan_mcp_review", plan_mcp_review_gate)
    graph.add_node("executor", executor)
    graph.add_node("deviation_review_gate", deviation_review_gate)
    graph.add_node("verifier", verifier)
    graph.add_node("formatter", formatter)
    graph.add_conditional_edges(START, _route_mode, {"fast": "fast_mcp_review", "plan": "planner"})
    graph.add_edge("fast_runner", END)
    graph.add_edge("planner", "plan_review")
    graph.add_edge("executor", "deviation_review_gate")
    graph.add_edge("verifier", "formatter")
    graph.add_edge("formatter", END)
    return graph.compile(checkpointer=checkpointer, store=store)


def _route_mode(state: RuntimeState) -> str:
    mode = state.get("mode")
    if mode not in {"fast", "plan"}:
        msg = "Runtime state mode must be `fast` or `plan`."
        raise ValueError(msg)
    return mode


def _normalize_gate_command(payload: object) -> Mapping[str, Any]:
    if isinstance(payload, Mapping):
        return payload
    return {"action": "cancel"}


def _last_user_content(state: RuntimeState) -> str:
    messages = state.get("messages", [])
    if not messages:
        msg = "Runtime state must include at least one user message."
        raise ValueError(msg)
    content = getattr(messages[-1], "content", "")
    return str(content)


def _tool_call_name(request: ToolCallRequest) -> str | None:
    name = request.tool_call.get("name") or getattr(request.tool, "name", None)
    return str(name) if name is not None else None


def _executor_write_todos_error(request: ToolCallRequest) -> ToolMessage:
    return ToolMessage(
        content=(
            "The write_todos tool is not available during approved plan execution. "
            "Complete the current item and return result evidence; the fsagent runtime updates todo status."
        ),
        tool_call_id=str(request.tool_call.get("id") or ""),
        status="error",
    )


@contextmanager
def _executor_todo_middleware_excluded(model: str | BaseChatModel) -> Iterator[None]:
    profile_key = _executor_harness_profile_key(model)
    if profile_key is None:
        yield
        return

    with _HARNESS_PROFILE_LOCK:
        _ensure_harness_profiles_loaded()
        original_profiles = dict(_HARNESS_PROFILES)
        try:
            register_harness_profile(
                profile_key,
                HarnessProfile(excluded_middleware=frozenset({"TodoListMiddleware"})),
            )
            yield
        finally:
            _HARNESS_PROFILES.clear()
            _HARNESS_PROFILES.update(original_profiles)


def _executor_harness_profile_key(model: str | BaseChatModel) -> str | None:
    if isinstance(model, str):
        return model
    identifier = get_model_identifier(model)
    provider = get_model_provider(model)
    if provider and identifier and ":" not in identifier:
        return f"{provider}:{identifier}"
    if identifier is not None and ":" in identifier:
        return identifier
    return provider


def _system_prompt_text(system_prompt: str | SystemMessage | None) -> str | None:
    if isinstance(system_prompt, SystemMessage):
        return str(system_prompt.content)
    return system_prompt


def _planner_repo_rules(system_prompt: str | SystemMessage | None) -> str | None:
    """Extract repo rules from system prompt for trusted context injection.

    If the system prompt looks like AGENTS.md format (contains multiple ## sections),
    extract only the rules sections. Otherwise, return the entire text as rules.
    """
    text = _system_prompt_text(system_prompt)
    if not text:
        return None

    text = text.strip()

    # Check if this looks like AGENTS.md format with multiple ## sections
    # If it has a main title (# ) followed by multiple ## sections, extract only rules
    lines = text.split("\n")
    has_main_title = any(line.startswith("# ") for line in lines)
    section_count = sum(1 for line in lines if line.startswith("## "))

    if has_main_title and section_count > 1:
        # AGENTS.md format - extract only rules sections
        # Exclude sections that are clearly not rules (like project background)
        result_lines = []
        in_rules_section = False

        # Sections that are NOT rules (project background, overview, etc.)
        non_rules_keywords = [
            "项目定位",
            "project positioning",
            "背景",
            "background",
            "简介",
            "introduction",
            "概述",
            "overview",
        ]

        for line in lines:
            if line.startswith("## "):
                # Check if this is a rules section
                section_title = line[3:].lower()

                # Exclude if it's explicitly a non-rules section
                in_rules_section = not any(keyword in section_title for keyword in non_rules_keywords)

                # Skip the section header itself
                continue

            if in_rules_section:
                result_lines.append(line)

        rules_text = "\n".join(result_lines).strip()
        return rules_text or None

    # Pure rules text - return as-is
    return text


def _join_prompts(system_prompt: str | SystemMessage | None, runtime_prompt: str) -> str | SystemMessage:
    if isinstance(system_prompt, SystemMessage):
        return SystemMessage(content=[*system_prompt.content_blocks, {"type": "text", "text": f"\n\n{runtime_prompt}"}])
    if system_prompt:
        return f"{system_prompt}\n\n{runtime_prompt}"
    return runtime_prompt


def _planner_system_prompt(
    *,
    system_prompt: str | SystemMessage | None,
    capability_summary: str,
) -> str | SystemMessage:
    return _join_prompts(
        system_prompt,
        f"{PLANNER_SYSTEM_PROMPT}\n\n{capability_summary}",
    )


def _progress_callback(config: RunnableConfig | None) -> ProgressCallback | None:
    metadata = (config or {}).get("metadata") or {}
    callback = metadata.get("fsagent_event_sink")
    return callback if callable(callback) else None


async def _emit_progress(config: RunnableConfig | None, event: Mapping[str, object]) -> None:
    callback = _progress_callback(config)
    if callback is not None:
        await callback(event)


async def _emit_planner_capability_warnings(config: RunnableConfig | None, warnings: Sequence[str]) -> None:
    for warning in warnings:
        await _emit_progress(
            config,
            {
                "kind": "planner.capability_warning",
                "message": warning,
                "warning": warning,
            },
        )


async def _emit_planner_context_warnings(config: RunnableConfig | None, warnings: Sequence[str]) -> None:
    for warning in warnings:
        await _emit_progress(
            config,
            {
                "kind": "planner.context_warning",
                "message": warning,
                "warning": warning,
            },
        )
