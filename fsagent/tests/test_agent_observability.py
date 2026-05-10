import pytest
from langchain.agents.middleware.types import ExtendedModelResponse, ModelRequest, ModelResponse, ToolCallRequest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool

from fsagent.runtime.agent_observability import (
    AgentLogContext,
    AgentObservabilityMiddleware,
    PlannerToolBoundaryMiddleware,
)


@tool
def observed_tool(value: str) -> str:
    """Return the supplied value."""
    return value


async def test_agent_observability_emits_model_start_and_completion_events():
    events: list[dict[str, object]] = []
    middleware = AgentObservabilityMiddleware(
        context=AgentLogContext(agent_mode="plan", phase="planner"),
        on_event=events.append,
    )
    request = ModelRequest(
        model=FakeListChatModel(responses=[""]),
        messages=[],
        tools=[observed_tool],
        state={"messages": []},
    )

    async def handler(_request: ModelRequest) -> ModelResponse:
        return ModelResponse(
            result=[
                AIMessage(
                    content="",
                    tool_calls=[{"name": "observed_tool", "args": {"value": "secret"}, "id": "call-1"}],
                )
            ]
        )

    await middleware.awrap_model_call(request, handler)

    assert [event["kind"] for event in events] == ["agent.model.started", "agent.model.completed"]
    assert events[0]["agent_mode"] == "plan"
    assert events[0]["phase"] == "planner"
    assert events[0]["available_tool_count"] == 1
    assert events[1]["tool_call_count"] == 1
    assert "duration_ms" in events[1]
    assert "secret" not in str(events)


async def test_agent_observability_counts_tool_calls_from_extended_model_response():
    events: list[dict[str, object]] = []
    middleware = AgentObservabilityMiddleware(
        context=AgentLogContext(agent_mode="fast", phase="fast_runner"),
        on_event=events.append,
    )
    request = ModelRequest(
        model=FakeListChatModel(responses=[""]),
        messages=[],
        tools=[observed_tool],
        state={"messages": []},
    )

    async def handler(_request: ModelRequest) -> ExtendedModelResponse:
        return ExtendedModelResponse(
            model_response=ModelResponse(
                result=[
                    AIMessage(
                        content="",
                        tool_calls=[{"name": "observed_tool", "args": {"value": "secret"}, "id": "call-1"}],
                    )
                ]
            )
        )

    await middleware.awrap_model_call(request, handler)

    assert events[-1]["kind"] == "agent.model.completed"
    assert events[-1]["tool_call_count"] == 1


async def test_agent_observability_emits_tool_events_with_summarized_payloads():
    events: list[dict[str, object]] = []
    middleware = AgentObservabilityMiddleware(
        context=AgentLogContext(agent_mode="plan", phase="executor", todo_index=2, todo_content="检查日志"),
        on_event=events.append,
    )
    request = ToolCallRequest(
        tool_call={"name": "observed_tool", "args": {"value": "secret"}, "id": "call-1"},
        tool=observed_tool,
        state={"messages": []},
        runtime=None,
    )

    async def handler(_request: ToolCallRequest) -> ToolMessage:
        return ToolMessage(content="tool result text", tool_call_id="call-1")

    await middleware.awrap_tool_call(request, handler)

    assert [event["kind"] for event in events] == ["agent.tool.started", "agent.tool.completed"]
    assert events[0]["phase"] == "executor"
    assert events[0]["todo_index"] == 2
    assert events[0]["todo_content"] == "检查日志"
    assert events[0]["tool_name"] == "observed_tool"
    assert events[0]["args_keys"] == ["value"]
    assert events[0]["args_size_chars"] > 0
    assert events[1]["result_size_chars"] == len("tool result text")
    assert "secret" not in str(events)


async def test_agent_observability_emits_failed_event_and_reraises():
    events: list[dict[str, object]] = []
    middleware = AgentObservabilityMiddleware(
        context=AgentLogContext(agent_mode="fast", phase="fast_runner"),
        on_event=events.append,
    )
    request = ModelRequest(
        model=FakeListChatModel(responses=[""]),
        messages=[],
        tools=[],
        state={"messages": []},
    )

    async def handler(_request: ModelRequest) -> ModelResponse:
        msg = "provider failed"
        raise RuntimeError(msg)

    with pytest.raises(RuntimeError, match="provider failed"):
        await middleware.awrap_model_call(request, handler)

    assert [event["kind"] for event in events] == ["agent.model.started", "agent.model.failed"]
    assert events[1]["error_type"] == "RuntimeError"
    assert events[1]["error"] == "provider failed"


async def test_planner_tool_boundary_blocks_non_write_todos_tools():
    events: list[dict[str, object]] = []
    middleware = PlannerToolBoundaryMiddleware(on_event=events.append)
    request = ToolCallRequest(
        tool_call={"name": "read_file", "args": {"file_path": "secret.txt"}, "id": "call-1"},
        tool=observed_tool,
        state={"messages": []},
        runtime=None,
    )

    async def handler(_request: ToolCallRequest) -> ToolMessage:
        return ToolMessage(content="should not run", tool_call_id="call-1")

    with pytest.raises(PermissionError, match="Planner is only allowed to call write_todos"):
        await middleware.awrap_tool_call(request, handler)

    assert [event["kind"] for event in events] == ["agent.tool.failed"]
    assert events[0]["phase"] == "planner"
    assert events[0]["tool_name"] == "read_file"
    assert events[0]["error_type"] == "PermissionError"
