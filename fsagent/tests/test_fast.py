from langchain.agents.middleware.types import ExtendedModelResponse, ModelRequest, ModelResponse
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from fsagent.runtime.fast import FastToolRoundMiddleware


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
    middleware = FastToolRoundMiddleware()
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
