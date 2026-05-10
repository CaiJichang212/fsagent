from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest
from langchain.agents.middleware import ModelCallLimitMiddleware, ToolCallLimitMiddleware
from langchain.agents.middleware.model_call_limit import ModelCallLimitExceededError
from langchain.agents.middleware.tool_call_limit import ToolCallLimitExceededError
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import LanguageModelInput
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool, tool
from pydantic import Field

from fsagent.runtime.graph import create_runtime
from fsagent.runtime.planner_agent import create_planner_agent
from fsagent.runtime.planner_capabilities import PlannerCapabilitySummaryBuilder


def test_planner_capability_summary_limits_items_and_reports_omissions():
    tools = []
    for index in range(5):

        @tool(f"external_tool_{index}")
        def external_tool(query: str, *, _index: int = index) -> str:
            """Search external systems."""
            return f"{_index}:{query}"

        tools.append(external_tool)

    summary = PlannerCapabilitySummaryBuilder(
        regular_tools=tools,
        skills=[],
        memory=[],
        backend=None,
        mcp_config_path=None,
        no_mcp=True,
        trust_project_mcp=None,
        item_limit=2,
    ).build()

    assert "external_tool_0 - Search external systems." in summary.text
    assert "external_tool_1 - Search external systems." in summary.text
    assert "external_tool_2" not in summary.text
    assert "+3 more" in summary.text
    assert summary.warnings == [
        "Deep Agents built-in executor tools truncated to 2 items; +6 more omitted.",
        "Caller-provided executor tools truncated to 2 items; +3 more omitted.",
    ]


async def test_planner_capability_summary_is_precomputed_when_runtime_is_created(tmp_path: Path):
    skill_source = tmp_path / "skills"
    first_skill = skill_source / "first-skill"
    first_skill.mkdir(parents=True)
    (first_skill / "SKILL.md").write_text(
        """---
name: first-skill
description: First skill visible at runtime creation.
---
""",
        encoding="utf-8",
    )

    model = _ToolBindingFakeChatModel(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "write_todos",
                            "args": {"todos": [{"content": "检查日志", "status": "pending"}]},
                            "id": "call-1",
                        }
                    ],
                ),
                AIMessage(content="计划已生成"),
            ]
        )
    )
    runtime = create_runtime(model=model, skills=[str(skill_source)], no_mcp=True)

    second_skill = skill_source / "second-skill"
    second_skill.mkdir()
    (second_skill / "SKILL.md").write_text(
        """---
name: second-skill
description: Skill added after runtime creation.
---
""",
        encoding="utf-8",
    )

    result = await runtime.ainvoke(
        {"mode": "plan", "messages": [HumanMessage(content="hello")]},
        config={"configurable": {"thread_id": "thread-1"}},
    )

    assert "__interrupt__" in result
    first_model_call = "\n".join(str(message.content) for message in model.call_messages[0])
    assert "first-skill - First skill visible at runtime creation." in first_model_call
    assert "second-skill" not in first_model_call


async def test_planner_capability_summary_warnings_are_emitted_as_progress_events():
    tools = []
    for index in range(21):

        @tool(f"external_tool_{index:02d}")
        def external_tool(query: str, *, _index: int = index) -> str:
            """Search external systems."""
            return f"{_index}:{query}"

        tools.append(external_tool)

    events: list[dict[str, object]] = []
    model = _ToolBindingFakeChatModel(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "write_todos",
                            "args": {"todos": [{"content": "检查日志", "status": "pending"}]},
                            "id": "call-1",
                        }
                    ],
                ),
                AIMessage(content="计划已生成"),
            ]
        )
    )
    runtime = create_runtime(model=model, tools=tools, no_mcp=True)

    async def event_sink(event: dict[str, object]) -> None:
        events.append(event)

    result = await runtime.ainvoke(
        {"mode": "plan", "messages": [HumanMessage(content="hello")]},
        config={"configurable": {"thread_id": "thread-1"}, "metadata": {"fsagent_event_sink": event_sink}},
    )

    assert "__interrupt__" in result
    warning_events = [event for event in events if event["kind"] == "planner.capability_warning"]
    assert warning_events == [
        {
            "kind": "planner.capability_warning",
            "message": "Caller-provided executor tools truncated to 20 items; +1 more omitted.",
            "warning": "Caller-provided executor tools truncated to 20 items; +1 more omitted.",
        }
    ]
    assert [event["kind"] for event in events].index("planner.capability_warning") < [
        event["kind"] for event in events
    ].index("planner.started")


def test_create_planner_agent_installs_official_limit_middleware(monkeypatch):
    captured_middleware: list[object] = []

    class FakePlannerAgent:
        pass

    def fake_create_agent(**kwargs: object) -> FakePlannerAgent:
        captured_middleware.extend(kwargs["middleware"])  # type: ignore[arg-type]
        return FakePlannerAgent()

    monkeypatch.setattr("fsagent.runtime.planner_agent.create_agent", fake_create_agent)

    agent = create_planner_agent(
        model="fake:model",
        system_prompt="plan only",
        on_event=None,
        checkpointer=None,
        store=None,
    )

    assert isinstance(agent, FakePlannerAgent)
    model_limits = [item for item in captured_middleware if isinstance(item, ModelCallLimitMiddleware)]
    tool_limits = [item for item in captured_middleware if isinstance(item, ToolCallLimitMiddleware)]
    assert len(model_limits) == 1
    assert model_limits[0].run_limit == 8
    assert model_limits[0].exit_behavior == "error"
    assert len(tool_limits) == 1
    assert tool_limits[0].tool_name == "write_todos"
    assert tool_limits[0].run_limit == 1
    assert tool_limits[0].exit_behavior == "error"


async def test_planner_agent_rejects_repeated_write_todos_tool_calls():
    model = _ToolBindingFakeChatModel(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "write_todos",
                            "args": {"todos": [{"content": "第一版计划", "status": "pending"}]},
                            "id": "call-1",
                        }
                    ],
                ),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "write_todos",
                            "args": {"todos": [{"content": "第二版计划", "status": "pending"}]},
                            "id": "call-2",
                        }
                    ],
                ),
            ]
        )
    )
    agent = create_planner_agent(
        model=model,
        system_prompt="plan only",
        on_event=None,
        checkpointer=None,
        store=None,
    )

    with pytest.raises(ToolCallLimitExceededError):
        await agent.ainvoke({"messages": [HumanMessage(content="hello")], "mode": "plan"})


async def test_planner_agent_rejects_model_call_when_run_limit_is_exceeded(monkeypatch):
    monkeypatch.setattr("fsagent.runtime.planner_agent.PLANNER_MODEL_CALL_RUN_LIMIT", 0)
    model = _ToolBindingFakeChatModel(messages=iter([AIMessage(content="不会被调用")]))
    agent = create_planner_agent(
        model=model,
        system_prompt="plan only",
        on_event=None,
        checkpointer=None,
        store=None,
    )

    with pytest.raises(ModelCallLimitExceededError):
        await agent.ainvoke({"messages": [HumanMessage(content="hello")], "mode": "plan"})


class _ToolBindingFakeChatModel(BaseChatModel):
    messages: Any = Field(exclude=True)
    bound_tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool] = ()
    call_messages: list[list[BaseMessage]] = Field(default_factory=list)

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: object,
    ) -> Runnable[LanguageModelInput, AIMessage]:
        _ = (tool_choice, kwargs)
        self.bound_tools = tools
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: object,
    ) -> ChatResult:
        _ = (stop, run_manager, kwargs)
        self.call_messages.append(messages)
        message = next(self.messages)
        ai_message = AIMessage(content=message) if isinstance(message, str) else message
        return ChatResult(generations=[ChatGeneration(message=ai_message)])

    @property
    def _llm_type(self) -> str:
        return "fsagent-test-tool-binding-fake-chat-model"
