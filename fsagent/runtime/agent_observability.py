"""Agent-level observability middleware for fast and plan runtimes."""

from __future__ import annotations

import inspect
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from time import perf_counter
from typing import TYPE_CHECKING, Any, Literal

from langchain.agents.middleware.types import (
    AgentMiddleware,
    ExtendedModelResponse,
    ModelRequest,
    ModelResponse,
    ToolCallRequest,
)
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage

from fsagent.runtime.state import RuntimeState

if TYPE_CHECKING:
    from langgraph.types import Command

AgentMode = Literal["fast", "plan"]
AgentPhase = Literal["fast_runner", "planner", "executor"]
ProgressEvent = Mapping[str, object]
ProgressCallback = Callable[[ProgressEvent], object | Awaitable[object]]


@dataclass(frozen=True, slots=True)
class AgentLogContext:
    """Stable context for one observable agent invocation."""

    agent_mode: AgentMode
    phase: AgentPhase
    todo_index: int | None = None
    todo_content: str | None = None


class PlannerToolBoundaryMiddleware(AgentMiddleware[RuntimeState, Any, Any]):
    """Prevent the planner from executing anything except writing the draft plan."""

    state_schema = RuntimeState

    def __init__(self, *, on_event: ProgressCallback | None = None) -> None:
        """Initialize the planner tool boundary."""
        self._context = AgentLogContext(agent_mode="plan", phase="planner")
        self._on_event = on_event

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        """Block non-planning tools before they can run."""
        tool_name = str(request.tool_call.get("name") or getattr(request.tool, "name", "unknown"))
        if tool_name == "write_todos":
            return await handler(request)

        error = PermissionError("Planner is only allowed to call write_todos before plan review.")
        args = request.tool_call.get("args")
        await _emit_async(
            self._on_event,
            _event_payload(
                context=self._context,
                kind="agent.tool.failed",
                message=f"planner 禁止调用工具 {tool_name}: {error}",
                state=request.state,
                fields={
                    "tool_name": tool_name,
                    "tool_call_id": str(request.tool_call.get("id") or ""),
                    "args_keys": _mapping_keys(args),
                    "args_size_chars": _serialized_size(args),
                    "duration_ms": 0.0,
                    "error": str(error),
                    "error_type": type(error).__name__,
                },
            ),
        )
        raise error


class AgentObservabilityMiddleware(AgentMiddleware[RuntimeState, Any, Any]):
    """Emit summarized model and tool lifecycle events for an agent."""

    state_schema = RuntimeState

    def __init__(self, *, context: AgentLogContext, on_event: ProgressCallback | None = None) -> None:
        """Initialize the middleware."""
        self._context = context
        self._on_event = on_event
        self._turn_index = 0

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any] | ExtendedModelResponse[Any]]],
    ) -> ModelResponse[Any] | ExtendedModelResponse[Any]:
        """Emit events around async model calls."""
        turn_index = self._next_turn_index()
        started = perf_counter()
        base_fields = {
            "turn_index": turn_index,
            "message_count": len(request.messages),
            "available_tool_count": len(request.tools),
        }
        await self._emit(
            "agent.model.started",
            f"{self._context.phase} 模型调用开始",
            state=request.state,
            **base_fields,
        )
        try:
            response = await handler(request)
        except Exception as exc:
            await self._emit(
                "agent.model.failed",
                f"{self._context.phase} 模型调用失败: {exc}",
                state=request.state,
                **base_fields,
                duration_ms=_duration_ms(started),
                error=str(exc),
                error_type=type(exc).__name__,
            )
            raise
        await self._emit(
            "agent.model.completed",
            f"{self._context.phase} 模型调用完成",
            state=request.state,
            **base_fields,
            duration_ms=_duration_ms(started),
            tool_call_count=_tool_call_count(_model_response_messages(response)),
        )
        return response

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        """Emit events around async tool calls."""
        started = perf_counter()
        tool_name = str(request.tool_call.get("name") or getattr(request.tool, "name", "unknown"))
        tool_call_id = str(request.tool_call.get("id") or "")
        args = request.tool_call.get("args")
        started_message, completed_message, failed_message = _tool_messages(self._context.phase, tool_name)
        base_fields = {
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "args_keys": _mapping_keys(args),
            "args_size_chars": _serialized_size(args),
        }
        await self._emit(
            "agent.tool.started",
            started_message,
            state=request.state,
            **base_fields,
        )
        try:
            response = await handler(request)
        except Exception as exc:
            await self._emit(
                "agent.tool.failed",
                f"{failed_message}: {exc}",
                state=request.state,
                **base_fields,
                duration_ms=_duration_ms(started),
                error=str(exc),
                error_type=type(exc).__name__,
            )
            raise
        await self._emit(
            "agent.tool.completed",
            completed_message,
            state=request.state,
            **base_fields,
            duration_ms=_duration_ms(started),
            result_size_chars=_tool_result_size(response),
        )
        return response

    async def emit_event(self, kind: str, message: str, **fields: object) -> None:
        """Emit a custom agent event with this middleware's context."""
        await self._emit(kind, message, **fields)

    def emit_event_sync(self, kind: str, message: str, **fields: object) -> None:
        """Best-effort sync event emission for sync middleware hooks."""
        _emit_sync(self._on_event, _event_payload(context=self._context, kind=kind, message=message, fields=fields))

    def _next_turn_index(self) -> int:
        self._turn_index += 1
        return self._turn_index

    async def _emit(
        self,
        kind: str,
        message: str,
        *,
        state: object | None = None,
        **fields: object,
    ) -> None:
        await _emit_async(
            self._on_event,
            _event_payload(context=self._context, kind=kind, message=message, fields=fields, state=state),
        )


def agent_event_payload(
    context: AgentLogContext,
    kind: str,
    message: str,
    *,
    state: object | None = None,
    **fields: object,
) -> dict[str, object]:
    """Build an agent event payload without emitting it."""
    return _event_payload(context=context, kind=kind, message=message, fields=fields, state=state)


async def emit_agent_event(
    on_event: ProgressCallback | None,
    context: AgentLogContext,
    kind: str,
    message: str,
    **fields: object,
) -> None:
    """Emit an agent event through a runtime progress callback."""
    await _emit_async(on_event, agent_event_payload(context, kind, message, **fields))


def emit_agent_event_sync(
    on_event: ProgressCallback | None,
    context: AgentLogContext,
    kind: str,
    message: str,
    **fields: object,
) -> None:
    """Best-effort sync emission for sync middleware hooks."""
    _emit_sync(on_event, agent_event_payload(context, kind, message, **fields))


def _event_payload(
    *,
    context: AgentLogContext,
    kind: str,
    message: str,
    fields: Mapping[str, object],
    state: object | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "kind": kind,
        "message": message,
        "agent_mode": context.agent_mode,
        "phase": context.phase,
        **fields,
    }
    todo_index = context.todo_index or _state_int(state, "fsagent_todo_index")
    todo_content = context.todo_content or _state_str(state, "fsagent_todo_content")
    if todo_index is not None:
        payload["todo_index"] = todo_index
    if todo_content is not None:
        payload["todo_content"] = todo_content
    return payload


async def _emit_async(callback: ProgressCallback | None, event: Mapping[str, object]) -> None:
    if callback is None:
        return
    result = callback(event)
    if inspect.isawaitable(result):
        await result


def _emit_sync(callback: ProgressCallback | None, event: Mapping[str, object]) -> None:
    if callback is None:
        return
    result = callback(event)
    if inspect.isawaitable(result):
        close = getattr(result, "close", None)
        if callable(close):
            close()


def _duration_ms(started: float) -> float:
    return round((perf_counter() - started) * 1000, 2)


def _tool_call_count(messages: Sequence[BaseMessage]) -> int:
    return sum(len(message.tool_calls) for message in messages if isinstance(message, AIMessage))


def _model_response_messages(response: ModelResponse[Any] | ExtendedModelResponse[Any]) -> Sequence[BaseMessage]:
    if isinstance(response, ExtendedModelResponse):
        return response.model_response.result
    return response.result


def _mapping_keys(value: object) -> list[str]:
    if not isinstance(value, Mapping):
        return []
    return sorted(str(key) for key in value)


def _serialized_size(value: object) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False, default=str))
    except TypeError:
        return len(str(value))


def _tool_result_size(value: ToolMessage | Command[Any]) -> int:
    if isinstance(value, ToolMessage):
        content = value.content
        if isinstance(content, str):
            return len(content)
        return _serialized_size(content)
    return _serialized_size(value)


def _tool_messages(phase: AgentPhase, tool_name: str) -> tuple[str, str, str]:
    if phase == "planner" and tool_name == "write_todos":
        return ("planner 写入计划草案", "planner 计划草案写入完成", "planner 计划草案写入失败")
    return (
        f"{phase} 调用工具 {tool_name}",
        f"{phase} 工具 {tool_name} 调用完成",
        f"{phase} 工具 {tool_name} 调用失败",
    )


def _state_int(state: object | None, key: str) -> int | None:
    if not isinstance(state, Mapping):
        return None
    value = state.get(key)
    return value if isinstance(value, int) else None


def _state_str(state: object | None, key: str) -> str | None:
    if not isinstance(state, Mapping):
        return None
    value = state.get(key)
    return value if isinstance(value, str) and value else None
