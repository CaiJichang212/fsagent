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
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.store.base import BaseStore
from langgraph.types import Checkpointer, Command

from fsagent.runtime.agent_observability import (
    AgentLogContext,
    ProgressCallback,
    emit_agent_event,
    emit_agent_event_sync,
)
from fsagent.runtime.prompts import FAST_SYSTEM_PROMPT
from fsagent.runtime.state import RuntimeState

_FAST_TOOL_LIMIT_FALLBACK = (
    "Fast 模式已使用过一次工具调用, 后续工具调用已被忽略。"
    "请切换 Plan 模式或重新发起任务以检查更多工具。"
)


class FastToolRoundMiddleware(AgentMiddleware[RuntimeState, Any, Any]):
    """Allow fast mode to use tools in at most one model turn."""

    state_schema = RuntimeState

    def __init__(
        self,
        *,
        context: AgentLogContext | None = None,
        on_event: ProgressCallback | None = None,
    ) -> None:
        """Initialize the middleware."""
        self._context = context or AgentLogContext(agent_mode="fast", phase="fast_runner")
        self._on_event = on_event

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
        self._emit_tools_disabled_if_needed(request, len(request.tools))
        response = handler(limited_request)
        if request.state.get("fast_tool_round_used", False):
            return self._without_post_round_tool_calls(response)
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
        await self._emit_tools_disabled_if_needed_async(request, len(request.tools))
        response = await handler(limited_request)
        if request.state.get("fast_tool_round_used", False):
            return self._without_post_round_tool_calls(response)
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

    @classmethod
    def _without_post_round_tool_calls(cls, response: ModelResponse[Any]) -> ModelResponse[Any]:
        if not cls._response_has_tool_calls(response):
            return response
        return ModelResponse(result=[cls._message_without_tool_calls(message) for message in response.result])

    @staticmethod
    def _message_without_tool_calls(message: BaseMessage) -> BaseMessage:
        if not isinstance(message, AIMessage) or not message.tool_calls:
            return message
        content = message.content or _FAST_TOOL_LIMIT_FALLBACK
        return AIMessage(content=content)

    def _emit_tools_disabled_if_needed(self, request: ModelRequest[Any], available_tool_count: int) -> None:
        if request.state.get("fast_tool_round_used", False) and available_tool_count > 0:
            emit_agent_event_sync(
                self._on_event,
                self._context,
                "agent.tools.disabled",
                "Fast 模式已使用过工具轮次, 后续模型调用禁用工具",
                available_tool_count=available_tool_count,
            )

    async def _emit_tools_disabled_if_needed_async(
        self,
        request: ModelRequest[Any],
        available_tool_count: int,
    ) -> None:
        if request.state.get("fast_tool_round_used", False) and available_tool_count > 0:
            await emit_agent_event(
                self._on_event,
                self._context,
                "agent.tools.disabled",
                "Fast 模式已使用过工具轮次, 后续模型调用禁用工具",
                available_tool_count=available_tool_count,
            )


def create_fast_agent(
    *,
    model: str | BaseChatModel,
    tools: Sequence[BaseTool | Callable[..., Any] | dict[str, Any]] | None = None,
    system_prompt: str | None = None,
    middleware: Sequence[AgentMiddleware[RuntimeState, Any, Any]] | None = None,
    checkpointer: Checkpointer | None = None,
    store: BaseStore | None = None,
    on_event: ProgressCallback | None = None,
) -> object:
    """Create a fast-mode agent without `TodoListMiddleware`.

    Args:
        model: Chat model or model identifier.
        tools: Tools available during the single permitted tool round.
        system_prompt: Optional caller prompt layered before the fast prompt.
        middleware: Additional agent middleware to install before the fast tool limiter.
        checkpointer: Optional checkpointer for HITL tool approvals.
        store: Optional LangGraph store.
        on_event: Optional progress callback for fast tool limiter events.

    Returns:
        Compiled LangChain agent.
    """
    prompt = f"{system_prompt}\n\n{FAST_SYSTEM_PROMPT}" if system_prompt else FAST_SYSTEM_PROMPT
    return create_agent(
        model=model,
        tools=list(tools or []),
        system_prompt=prompt,
        middleware=[
            *(middleware or []),
            FastToolRoundMiddleware(
                context=AgentLogContext(agent_mode="fast", phase="fast_runner"),
                on_event=on_event,
            ),
        ],
        checkpointer=checkpointer,
        store=store,
    )


async def run_fast(
    *,
    agent: object,
    content: str,
    config: RunnableConfig | None = None,
    on_event: ProgressCallback | None = None,
) -> str:
    """Run a fast-mode request and return the final assistant text.

    Args:
        agent: Fast agent with `ainvoke`.
        content: User task.
        config: Optional LangGraph runnable config.
        on_event: Optional progress callback for fast lifecycle events.

    Returns:
        Final text response.
    """
    context = AgentLogContext(agent_mode="fast", phase="fast_runner")
    await emit_agent_event(on_event, context, "agent.started", "Fast agent 启动", message_length=len(content))
    result = await agent.ainvoke(  # type: ignore[attr-defined]
        {"messages": [HumanMessage(content=content)], "mode": "fast", "fast_tool_round_used": False},
        config=config,
    )
    final = _extract_text(result)
    await emit_agent_event(
        on_event,
        context,
        "agent.completed",
        "Fast agent 完成",
        final_response_length=len(final),
    )
    return final


def _extract_text(result: object) -> str:
    messages = result.get("messages", []) if isinstance(result, dict) else []
    for message in reversed(messages):
        content = getattr(message, "content", None)
        if isinstance(content, str) and content:
            return content
    final_response = result.get("final_response") if isinstance(result, dict) else None
    return str(final_response or "")
