"""Stateful service that adapts the LangGraph runtime to HTTP sessions."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from langchain_core.messages import HumanMessage

from fsagent.api._langgraph_compat import Command, InMemorySaver
from fsagent.api.schemas import (
    ExecutionLogEntry,
    PlanMeta,
    ReviewRequest,
    RunRequest,
    SessionResponse,
    TimelineEvent,
    TodoItem,
)
from fsagent.observability import RunLogContext, RunLogger, get_logger
from fsagent.runtime.graph import create_runtime
from fsagent.runtime.model_config import FsAgentEnv, build_chat_qwen, resolve_thinking_enabled

logger = get_logger(__name__)


class RuntimeLike(Protocol):
    """Runtime object with async LangGraph-style invocation."""

    async def ainvoke(self, payload: object, config: Mapping[str, object] | None = None) -> Mapping[str, object]:
        """Invoke or resume the runtime."""


RuntimeFactory = Callable[..., RuntimeLike]


class SessionNotFoundError(KeyError):
    """Raised when a requested API session does not exist."""


@dataclass(slots=True)
class SessionRecord:
    """Server-side data needed to resume a runtime."""

    session: SessionResponse
    runtime: RuntimeLike
    config: dict[str, object]
    logger: RunLogger


class FsAgentApiService:
    """In-memory API service for frontend-driven fsagent sessions."""

    def __init__(self, runtime_factory: RuntimeFactory | None = None) -> None:
        """Initialize the service with an optional runtime factory."""
        self._runtime_factory = runtime_factory or self._default_runtime_factory
        self._sessions: dict[str, SessionRecord] = {}

    async def create_run(self, request: RunRequest) -> SessionResponse:
        """Create and invoke a Fast or Plan run."""
        record = self._create_record(request)
        payload = {"mode": request.mode, "messages": [HumanMessage(content=request.message)]}
        await self._invoke_once(record, payload)
        return record.session

    async def create_run_stream(self, request: RunRequest) -> AsyncIterator[SessionResponse]:
        """Create and invoke a run while yielding incremental session snapshots."""
        record = self._create_record(request)
        payload = {"mode": request.mode, "messages": [HumanMessage(content=request.message)]}
        async for session in self._invoke_stream(record, payload):
            yield session

    async def get_run(self, session_id: str) -> SessionResponse:
        """Return a stored session snapshot."""
        return self._record(session_id).session

    async def review_plan(self, session_id: str, request: ReviewRequest) -> SessionResponse:
        """Resume a plan-review interrupt with the selected user action."""
        record = self._record(session_id)
        self._append_event(
            record,
            _review_event_kind(request.action),
            _review_event_message(request),
            review_action=request.action,
        )
        record.session.updated_at = _now()
        if request.action == "cancel":
            record.session.status = "cancelled"
            record.session.error = request.reason
            return record.session

        payload = request.model_dump(by_alias=False, exclude_none=True)
        await self._invoke_once(record, Command(resume=payload))
        return record.session

    async def review_plan_stream(self, session_id: str, request: ReviewRequest) -> AsyncIterator[SessionResponse]:
        """Resume plan review while yielding incremental session snapshots."""
        record = self._record(session_id)
        self._append_event(
            record,
            _review_event_kind(request.action),
            _review_event_message(request),
            review_action=request.action,
        )
        record.session.updated_at = _now()
        if request.action == "cancel":
            record.session.status = "cancelled"
            record.session.error = request.reason
            yield _snapshot(record.session)
            return

        record.session.status = _pending_review_status(request.action)
        yield _snapshot(record.session)
        payload = Command(resume=request.model_dump(by_alias=False, exclude_none=True))
        async for session in self._invoke_stream(record, payload, yield_initial=False):
            yield session

    def _record(self, session_id: str) -> SessionRecord:
        try:
            return self._sessions[session_id]
        except KeyError as exc:
            raise SessionNotFoundError(session_id) from exc

    def _create_record(self, request: RunRequest) -> SessionRecord:
        session_id = _uid()
        thread_id = _uid()
        base_env = FsAgentEnv.from_sources()
        model = request.model or base_env.model
        env = _env_for_model(base_env, model)
        thinking = resolve_thinking_enabled(env, requested=request.thinking)
        session = SessionResponse(
            sessionId=session_id,
            threadId=thread_id,
            mode=request.mode,
            status="running" if request.mode == "fast" else "planning",
            message=request.message,
            model=model,
            thinking=thinking,
            mcpEnabled=request.mcp_enabled,
            trustProjectMcp=request.trust_project_mcp,
            todos=[],
            executionLog=[],
            timeline=[],
            createdAt=_now(),
            updatedAt=_now(),
        )
        saver = InMemorySaver()
        runtime = self._make_runtime(request, saver)
        run_logger = RunLogger(
            logger=logger,
            context=RunLogContext(
                session_id=session_id,
                thread_id=thread_id,
                mode=request.mode,
                model=model,
            ),
        )
        record = SessionRecord(
            session=session,
            runtime=runtime,
            config={"configurable": {"thread_id": thread_id}},
            logger=run_logger,
        )
        self._sessions[session_id] = record
        self._append_event(
            record,
            "session.created",
            f"创建会话 ({request.mode.upper()} 模式)",
            mcp_enabled=request.mcp_enabled,
            trust_project_mcp=request.trust_project_mcp,
            user_message_length=len(request.message),
        )
        self._append_event(record, "run.started", f"runtime 启动 · model={model}")
        return record

    def _make_runtime(self, request: RunRequest, checkpointer: InMemorySaver) -> RuntimeLike:
        parameters = inspect.signature(self._runtime_factory).parameters
        if len(parameters) == 1:
            return self._runtime_factory(request)
        return self._runtime_factory(request, checkpointer)

    def _apply_runtime_result(self, record: SessionRecord, result: Mapping[str, object]) -> None:
        interrupt = _first_interrupt_value(result.get("__interrupt__"))
        if isinstance(interrupt, Mapping) and interrupt.get("kind") == "plan_review":
            record.session.status = "awaiting_plan_review"
            record.session.todos = _todos(interrupt.get("todos"))
            record.session.plan_meta = _plan_meta(interrupt.get("plan_meta"))
            message = str(interrupt.get("instructions") or "等待用户审核计划")
            self._append_event(record, "interrupt.plan_review", message)
            record.session.updated_at = _now()
            return

        record.session.todos = _todos(result.get("todos"), fallback=record.session.todos)
        record.session.execution_log = _execution_log(result.get("execution_log"))
        record.session.final_response = str(result.get("final_response") or "")
        record.session.status = "completed"
        self._append_event(
            record,
            "run.completed",
            "运行完成",
            final_response_length=len(record.session.final_response or ""),
        )
        record.session.updated_at = _now()

    def _apply_progress_event(self, record: SessionRecord, event: Mapping[str, object]) -> None:
        kind = str(event.get("kind") or "run.started")
        message = str(event.get("message") or kind)
        if kind == "planner.started":
            record.session.status = "planning"
        elif kind == "planner.completed":
            record.session.todos = _todos(event.get("todos"), fallback=record.session.todos)
            record.session.plan_meta = _plan_meta(event.get("plan_meta"))
        elif kind in {"todo.started", "todo.completed", "todo.failed"}:
            record.session.status = "executing"
            record.session.todos = _todos(event.get("todos"), fallback=record.session.todos)
            record.session.execution_log = _execution_log(event.get("execution_log"))
        self._append_event(record, kind, message)
        record.session.updated_at = _now()

    async def _invoke_once(self, record: SessionRecord, payload: object) -> None:
        try:
            result = await record.runtime.ainvoke(payload, config=self._runtime_config(record))
        except Exception as exc:  # noqa: BLE001
            self._mark_failed(record, exc)
            return

        self._apply_runtime_result(record, result)

    async def _invoke_stream(
        self,
        record: SessionRecord,
        payload: object,
        *,
        yield_initial: bool = True,
    ) -> AsyncIterator[SessionResponse]:
        queue: asyncio.Queue[SessionResponse | None] = asyncio.Queue()

        async def on_progress() -> None:
            await queue.put(_snapshot(record.session))

        async def run() -> None:
            try:
                result = await record.runtime.ainvoke(
                    payload,
                    config=self._runtime_config(record, on_progress=on_progress),
                )
            except Exception as exc:  # noqa: BLE001
                self._mark_failed(record, exc)
            else:
                self._apply_runtime_result(record, result)
            await queue.put(_snapshot(record.session))
            await queue.put(None)

        task = asyncio.create_task(run())
        if yield_initial:
            yield _snapshot(record.session)
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield item
        finally:
            if not task.done():
                task.cancel()

    def _mark_failed(self, record: SessionRecord, error: Exception) -> None:
        record.session.status = "failed"
        record.session.error = str(error)
        self._append_event(record, "run.failed", str(error), error_type=type(error).__name__, exception=error)
        record.session.updated_at = _now()

    def _append_event(self, record: SessionRecord, kind: str, message: str, **fields: object) -> None:
        record.session.timeline.append(_event(kind, message))
        log_fields = {**_session_log_fields(record.session), **fields}
        exception = log_fields.pop("exception", None)
        if isinstance(exception, Exception):
            record.logger.exception(kind, message, exception, **log_fields)
        else:
            record.logger.event(kind, message, **log_fields)

    def _runtime_config(
        self,
        record: SessionRecord,
        *,
        on_progress: Callable[[], object] | None = None,
    ) -> dict[str, object]:
        async def event_sink(event: Mapping[str, object]) -> None:
            self._apply_progress_event(record, event)
            if on_progress is not None:
                callback_result = on_progress()
                if inspect.isawaitable(callback_result):
                    await callback_result

        return {
            **record.config,
            "metadata": {
                **dict(record.config.get("metadata") or {}),
                "fsagent_event_sink": event_sink,
            },
        }

    @staticmethod
    def _default_runtime_factory(request: RunRequest, checkpointer: InMemorySaver) -> RuntimeLike:
        env = FsAgentEnv.from_sources()
        if request.model:
            env = _env_for_model(env, request.model)
        return create_runtime(
            model=build_chat_qwen(env, thinking=request.thinking),
            checkpointer=checkpointer,
            mcp_config_path=request.mcp_config_path if request.mcp_enabled else None,
            no_mcp=not request.mcp_enabled,
            trust_project_mcp=request.trust_project_mcp,
        )


def _env_for_model(env: FsAgentEnv, model: str) -> FsAgentEnv:
    return FsAgentEnv(
        model=model,
        base_url=env.base_url,
        api_key=env.api_key,
        available_models_json=env.available_models_json,
        env_dir=env.env_dir,
    )


def _first_interrupt_value(value: object) -> object | None:
    interrupt = value
    if isinstance(value, list | tuple):
        if not value:
            return None
        interrupt = value[0]
    return getattr(interrupt, "value", interrupt)


def _todos(value: object, *, fallback: list[TodoItem] | None = None) -> list[TodoItem]:
    if not isinstance(value, list):
        return fallback or []
    todos = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        status = str(item.get("status") or "pending")
        if status == "blocked":
            status = "failed"
        todos.append(TodoItem(content=str(item.get("content") or ""), status=status))
    return todos


def _plan_meta(value: object) -> PlanMeta | None:
    if not isinstance(value, Mapping):
        return None
    return PlanMeta(
        goal=str(value.get("goal") or ""),
        assumptions=[str(item) for item in value.get("assumptions") or []],
        final_output_format=value.get("final_output_format") or value.get("finalOutputFormat"),
    )


def _execution_log(value: object) -> list[ExecutionLogEntry]:
    if not isinstance(value, list):
        return []
    entries = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        status = str(item.get("status") or "pending")
        if status == "blocked":
            status = "failed"
        entries.append(
            ExecutionLogEntry(
                content=str(item.get("content") or ""),
                status=status,
                result=item.get("result"),
                error=item.get("error"),
            )
        )
    return entries


def _review_event_kind(action: str) -> str:
    return {
        "approve": "plan.approved",
        "edit": "plan.edited",
        "retry": "plan.retrying",
        "cancel": "plan.cancelled",
    }[action]


def _review_event_message(request: ReviewRequest) -> str:
    if request.action == "retry":
        return f"用户请求重新生成计划{f': {request.feedback}' if request.feedback else ''}"
    if request.action == "edit":
        return f"用户编辑计划, 提交 {len(request.todos or [])} 项 todos"
    if request.action == "cancel":
        return f"用户取消{f': {request.reason}' if request.reason else ''}"
    return "用户批准计划"


def _pending_review_status(action: str) -> str:
    return {
        "approve": "executing",
        "edit": "editing_plan",
        "retry": "retrying_plan",
    }.get(action, "awaiting_plan_review")


def _snapshot(session: SessionResponse) -> SessionResponse:
    return session.model_copy(deep=True)


def _session_log_fields(session: SessionResponse) -> dict[str, object]:
    return {
        "status": session.status,
        "todo_count": len(session.todos),
        "execution_log_count": len(session.execution_log),
        "timeline_count": len(session.timeline),
    }


def _event(kind: str, message: str) -> TimelineEvent:
    return TimelineEvent(id=_uid(), kind=kind, message=message, at=_now())


def _uid() -> str:
    return uuid4().hex[:12]


def _now() -> str:
    return datetime.now(UTC).isoformat()
