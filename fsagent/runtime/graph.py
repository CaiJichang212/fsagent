"""LangGraph assembly for the explicit fast/plan runtime."""

from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from deepagents import create_deep_agent
from deepagents.backends.protocol import BackendFactory, BackendProtocol
from langchain.agents.middleware import HumanInTheLoopMiddleware, InterruptOnConfig
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage
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
from fsagent.runtime.executor import execute_plan
from fsagent.runtime.fast import create_fast_agent, run_fast
from fsagent.runtime.formatter import format_final_report
from fsagent.runtime.mcp import load_runtime_mcp_tools, resolve_mcp_config_path
from fsagent.runtime.planner import generate_plan
from fsagent.runtime.planner_agent import create_planner_agent
from fsagent.runtime.planner_capabilities import PlannerCapabilitySummaryBuilder
from fsagent.runtime.prompts import (
    EXECUTOR_SYSTEM_PROMPT,
    PLANNER_SYSTEM_PROMPT,
)
from fsagent.runtime.state import RuntimeState

ProgressCallback = Callable[[Mapping[str, object]], Awaitable[None]]


def create_runtime(  # noqa: C901
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

    async def fast_runner(state: RuntimeState, config: RunnableConfig | None = None) -> dict[str, Any]:
        on_event = _progress_callback(config)
        agent = create_fast_agent(
            model=model,
            tools=await combined_tools(),
            system_prompt=_system_prompt_text(system_prompt),
            middleware=[
                AgentObservabilityMiddleware(
                    context=AgentLogContext(agent_mode="fast", phase="fast_runner"),
                    on_event=on_event,
                )
            ],
            on_event=on_event,
        )
        content = _last_user_content(state)
        final = await run_fast(agent=agent, content=content, config=config, on_event=on_event)
        return {"final_response": final}

    async def planner(state: RuntimeState, config: RunnableConfig | None = None) -> dict[str, Any]:
        await _emit_planner_capability_warnings(config, planner_capability_summary.warnings)
        await _emit_progress(config, {"kind": "planner.started", "message": "开始生成计划"})
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
            content=_last_user_content(state),
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
            return Command(goto="executor")
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
        middleware = [
            AgentObservabilityMiddleware(
                context=AgentLogContext(agent_mode="plan", phase="executor"),
                on_event=on_event,
            )
        ]
        if interrupt_on is not None:
            middleware.append(HumanInTheLoopMiddleware(interrupt_on))
        agent = create_deep_agent(
            model=model,
            tools=await combined_tools(),
            system_prompt=_join_prompts(system_prompt, EXECUTOR_SYSTEM_PROMPT),
            middleware=middleware,
            skills=skills,
            memory=memory,
            backend=backend,
            interrupt_on=interrupt_on,
            checkpointer=checkpointer,
            store=store,
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
        return {"todos": result.todos, "execution_log": result.execution_log, "final_response": result.final_result}

    def formatter(state: RuntimeState) -> dict[str, Any]:
        report = format_final_report(
            result=state.get("final_response") or "Plan execution did not produce a final result.",
            todos=state["todos"],
            execution_log=state.get("execution_log", []),
            artifacts=state.get("artifacts", []),
        )
        return {"final_response": report}

    graph = StateGraph(RuntimeState)
    graph.add_node("fast_runner", fast_runner)
    graph.add_node("planner", planner)
    graph.add_node("plan_review", plan_review)
    graph.add_node("executor", executor)
    graph.add_node("formatter", formatter)
    graph.add_conditional_edges(START, _route_mode, {"fast": "fast_runner", "plan": "planner"})
    graph.add_edge("fast_runner", END)
    graph.add_edge("planner", "plan_review")
    graph.add_edge("executor", "formatter")
    graph.add_edge("formatter", END)
    return graph.compile(checkpointer=checkpointer, store=store)


def _route_mode(state: RuntimeState) -> str:
    mode = state.get("mode")
    if mode not in {"fast", "plan"}:
        msg = "Runtime state mode must be `fast` or `plan`."
        raise ValueError(msg)
    return mode


def _last_user_content(state: RuntimeState) -> str:
    messages = state.get("messages", [])
    if not messages:
        msg = "Runtime state must include at least one user message."
        raise ValueError(msg)
    content = getattr(messages[-1], "content", "")
    return str(content)


def _system_prompt_text(system_prompt: str | SystemMessage | None) -> str | None:
    if isinstance(system_prompt, SystemMessage):
        return str(system_prompt.content)
    return system_prompt


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
