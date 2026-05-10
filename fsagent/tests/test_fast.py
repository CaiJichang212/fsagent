from langchain.agents.middleware.types import ExtendedModelResponse, ModelRequest, ModelResponse
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from fsagent.runtime.agent_observability import AgentLogContext
from fsagent.runtime.fast import FastToolRoundMiddleware, run_fast


@tool
def sample_tool(value: str) -> str:
    """Return the supplied value."""
    return value


def test_fast_tool_round_middleware_allows_first_tool_round_and_marks_used():
    middleware = FastToolRoundMiddleware()
    seen_tool_counts: list[int] = []
    request = ModelRequest(
        model=FakeListChatModel(responses=[""]),
        messages=[],
        tools=[sample_tool],
        state={"messages": [], "fast_tool_round_used": False},
    )

    def handler(next_request: ModelRequest) -> ModelResponse:
        seen_tool_counts.append(len(next_request.tools))
        return ModelResponse(
            result=[AIMessage(content="", tool_calls=[{"name": "sample_tool", "args": {"value": "x"}, "id": "1"}])]
        )

    response = middleware.wrap_model_call(request, handler)

    assert seen_tool_counts == [1]
    assert isinstance(response, ExtendedModelResponse)
    assert response.command is not None
    assert response.command.update == {"fast_tool_round_used": True}


def test_fast_tool_round_middleware_disables_tools_after_first_round():
    events: list[dict[str, object]] = []
    middleware = FastToolRoundMiddleware(
        context=AgentLogContext(agent_mode="fast", phase="fast_runner"),
        on_event=events.append,
    )
    seen_tool_counts: list[int] = []
    request = ModelRequest(
        model=FakeListChatModel(responses=[""]),
        messages=[],
        tools=[sample_tool],
        state={"messages": [], "fast_tool_round_used": True},
    )

    def handler(next_request: ModelRequest) -> ModelResponse:
        seen_tool_counts.append(len(next_request.tools))
        return ModelResponse(result=[AIMessage(content="final")])

    response = middleware.wrap_model_call(request, handler)

    assert seen_tool_counts == [0]
    assert isinstance(response, ModelResponse)
    assert response.result == [AIMessage(content="final")]
    assert events == [
        {
            "kind": "agent.tools.disabled",
            "message": "Fast 模式已使用过工具轮次, 后续模型调用禁用工具",
            "agent_mode": "fast",
            "phase": "fast_runner",
            "available_tool_count": 1,
        }
    ]


async def test_run_fast_emits_agent_started_and_completed_events():
    events: list[dict[str, object]] = []

    class FakeAgent:
        async def ainvoke(self, _payload: object, config: object = None) -> dict[str, object]:
            assert config is None
            return {"messages": [AIMessage(content="fast result")]}

    result = await run_fast(agent=FakeAgent(), content="hello", on_event=events.append)

    assert result == "fast result"
    assert [event["kind"] for event in events] == ["agent.started", "agent.completed"]
    assert events[0]["agent_mode"] == "fast"
    assert events[0]["phase"] == "fast_runner"
    assert events[0]["message_length"] == len("hello")
    assert events[1]["final_response_length"] == len("fast result")
