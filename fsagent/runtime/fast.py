"""Fast-mode runner and tool-round limiter."""

from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware.types import (
    AgentMiddleware,
    ExtendedModelResponse,
    ModelRequest,
    ModelResponse,
)
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.types import Command

from fsagent.runtime.prompts import FAST_SYSTEM_PROMPT
from fsagent.runtime.state import RuntimeState


class FastToolRoundMiddleware(AgentMiddleware[RuntimeState, Any, Any]):
    """Allow fast mode to use tools in at most one model turn."""

    state_schema = RuntimeState

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelResponse[Any] | ExtendedModelResponse[Any]:
        """Disable tools after the first tool-calling model response.

        Args:
            request: Model request.
            handler: Wrapped model call.

        Returns:
            Model response, optionally with a state update marking the tool round used.
        """
        limited_request = self._disable_tools_if_used(request)
        response = handler(limited_request)
        if self._response_has_tool_calls(response) and not request.state.get("fast_tool_round_used", False):
            return ExtendedModelResponse(
                model_response=response,
                command=Command(update={"fast_tool_round_used": True}),
            )
        return response

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any] | ExtendedModelResponse[Any]:
        """Async variant of `wrap_model_call`."""
        limited_request = self._disable_tools_if_used(request)
        response = await handler(limited_request)
        if self._response_has_tool_calls(response) and not request.state.get("fast_tool_round_used", False):
            return ExtendedModelResponse(
                model_response=response,
                command=Command(update={"fast_tool_round_used": True}),
            )
        return response

    @staticmethod
    def _disable_tools_if_used(request: ModelRequest[Any]) -> ModelRequest[Any]:
        if request.state.get("fast_tool_round_used", False):
            return request.override(tools=[], tool_choice=None)
        return request

    @staticmethod
    def _response_has_tool_calls(response: ModelResponse[Any]) -> bool:
        return any(isinstance(message, AIMessage) and message.tool_calls for message in response.result)


def create_fast_agent(
    *,
    model: str | BaseChatModel,
    tools: Sequence[BaseTool | Callable[..., Any] | dict[str, Any]] | None = None,
    system_prompt: str | None = None,
) -> object:
    """Create a fast-mode agent without `TodoListMiddleware`.

    Args:
        model: Chat model or model identifier.
        tools: Tools available during the single permitted tool round.
        system_prompt: Optional caller prompt layered before the fast prompt.

    Returns:
        Compiled LangChain agent.
    """
    prompt = f"{system_prompt}\n\n{FAST_SYSTEM_PROMPT}" if system_prompt else FAST_SYSTEM_PROMPT
    return create_agent(
        model=model,
        tools=list(tools or []),
        system_prompt=prompt,
        middleware=[FastToolRoundMiddleware()],
    )


async def run_fast(
    *,
    agent: object,
    content: str,
    config: RunnableConfig | None = None,
) -> str:
    """Run a fast-mode request and return the final assistant text.

    Args:
        agent: Fast agent with `ainvoke`.
        content: User task.
        config: Optional LangGraph runnable config.

    Returns:
        Final text response.
    """
    result = await agent.ainvoke(  # type: ignore[attr-defined]
        {"messages": [HumanMessage(content=content)], "mode": "fast", "fast_tool_round_used": False},
        config=config,
    )
    return _extract_text(result)


def _extract_text(result: object) -> str:
    messages = result.get("messages", []) if isinstance(result, dict) else []
    for message in reversed(messages):
        content = getattr(message, "content", None)
        if isinstance(content, str) and content:
            return content
    final_response = result.get("final_response") if isinstance(result, dict) else None
    return str(final_response or "")
