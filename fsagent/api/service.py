"""Stateful service that adapts the LangGraph runtime to HTTP sessions."""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Iterator, Protocol
from uuid import uuid4

from langchain_core.messages import HumanMessage

from fsagent.api._langgraph_compat import Command, InMemorySaver
from fsagent.api.persistence import (
    InMemorySessionStore,
    SessionStore,
    SessionStoreNotFoundError,
    SessionStoreRecord,
)
from fsagent.api.schemas import (
    ArtifactRecord,
    EvidenceRecord,
    ExecutionLogEntry,
    PlanMeta,
    ReviewDecisionRequest,
    ReviewRecord,
    ReviewRequest,
    RunRequest,
    SessionResponse,
    TimelineEvent,
    TodoItem,
    ToolCallRecord,
    VerificationRecord,
)
from fsagent.langfuse_integration import LangfuseBridge, _summarize_payload_for_langfuse
from fsagent.observability import RunLogContext, RunLogger, get_logger
from fsagent.runtime.assembly import RuntimeAssemblyConfig, resolve_runtime_assembly
from fsagent.runtime.graph import create_runtime
from fsagent.runtime.model_config import FsAgentEnv, build_chat_qwen, resolve_thinking_enabled
from fsagent.runtime.policy import evaluate_tool_policy
from fsagent.runtime.risk import is_high_risk_tool_text

logger = get_logger(__name__)


class RuntimeLike(Protocol):
    """Runtime object with async LangGraph-style invocation."""

    async def ainvoke(self, payload: object, config: Mapping[str, object] | None = None) -> Mapping[str, object]:
        """Invoke or resume the runtime."""


RuntimeFactory = Callable[..., RuntimeLike]


class SessionNotFoundError(KeyError):
    """Raised when a requested API session does not exist."""


class ReviewNotFoundError(KeyError):
    """Raised when a requested review does not exist."""


class ReviewConflictError(ValueError):
    """Raised when a review decision does not target the pending review."""


class CheckpointMissingError(RuntimeError):
    """Raised when a stored session cannot be resumed because runtime state is unavailable."""


@dataclass(slots=True)
class SessionRecord:
    """Server-side data needed to resume a runtime."""

    session: SessionResponse
    runtime: RuntimeLike
    config: dict[str, object]
    logger: RunLogger


class FsAgentApiService:
    """In-memory API service for frontend-driven fsagent sessions."""

    def __init__(
        self,
        runtime_factory: RuntimeFactory | None = None,
        *,
        session_store: SessionStore | None = None,
        checkpoint_ref: str | None = None,
        langfuse_bridge: LangfuseBridge | None = None,
    ) -> None:
        """Initialize the service with an optional runtime factory."""
        self._runtime_factory = runtime_factory or self._default_runtime_factory
        self._session_store = session_store or InMemorySessionStore()
        self._checkpoint_ref = checkpoint_ref
        self._langfuse = langfuse_bridge or LangfuseBridge()
        self._sessions: dict[str, SessionRecord] = {}

    def shutdown(self) -> None:
        """Shutdown service-owned integrations."""
        self._langfuse.shutdown()

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
        record = self._sessions.get(session_id)
        if record is not None:
            return record.session
        try:
            return self._session_store.get(session_id).session
        except SessionStoreNotFoundError as exc:
            raise SessionNotFoundError(session_id) from exc

    async def review_plan(self, session_id: str, request: ReviewRequest) -> SessionResponse:
        """Resume a plan-review interrupt with the selected user action."""
        record, review = self._plan_review_context(session_id, request)
        self._append_event(
            record,
            _review_event_kind(request.action),
            _review_event_message(request),
            review_id=review.id,
            review_action=request.action,
        )
        _mark_review_decided(record, review, action=request.action, decision=_review_request_decision(request))
        record.session.updated_at = _now()
        if request.action == "cancel":
            record.session.status = "cancelled"
            record.session.error = request.reason
            record.session.pending_review = None
            self._persist(record)
            return record.session

        payload = request.model_dump(by_alias=False, exclude_none=True)
        payload.pop("review_id", None)
        await self._invoke_once(record, Command(resume=payload))
        return record.session

    def review_plan_stream(self, session_id: str, request: ReviewRequest) -> AsyncIterator[SessionResponse]:
        """Resume plan review while yielding incremental session snapshots."""
        record, review = self._plan_review_context(session_id, request)

        async def stream() -> AsyncIterator[SessionResponse]:
            self._append_event(
                record,
                _review_event_kind(request.action),
                _review_event_message(request),
                review_id=review.id,
                review_action=request.action,
            )
            _mark_review_decided(record, review, action=request.action, decision=_review_request_decision(request))
            record.session.updated_at = _now()
            if request.action == "cancel":
                record.session.status = "cancelled"
                record.session.error = request.reason
                record.session.pending_review = None
                self._persist(record)
                yield _snapshot(record.session)
                return

            record.session.status = _pending_review_status(request.action)
            self._persist(record)
            yield _snapshot(record.session)
            payload_data = request.model_dump(by_alias=False, exclude_none=True)
            payload_data.pop("review_id", None)
            payload = Command(resume=payload_data)
            async for session in self._invoke_stream(record, payload, yield_initial=False):
                yield session

        return stream()

    async def decide_review(
        self,
        session_id: str,
        review_id: str,
        request: ReviewDecisionRequest,
    ) -> SessionResponse:
        """Resume a generic review interrupt with a matching review id."""
        record, review, resume_payload = self._review_decision_context(session_id, review_id, request)
        self._append_event(
            record,
            _generic_review_event_kind(review.kind, request.action),
            _generic_review_event_message(review, request),
            review_id=review.id,
            review_kind=review.kind,
            review_action=request.action,
        )
        _mark_review_decided(record, review, action=request.action, decision=_review_decision_payload(request))
        record.session.updated_at = _now()
        if request.action == "cancel":
            record.session.status = "cancelled"
            record.session.error = request.reason
            record.session.pending_review = None
            self._persist(record)
            return record.session

        await self._invoke_once(
            record,
            Command(resume=resume_payload),
            default_status=_review_resume_default_status(request.action),
        )
        return record.session

    def decide_review_stream(
        self,
        session_id: str,
        review_id: str,
        request: ReviewDecisionRequest,
    ) -> AsyncIterator[SessionResponse]:
        """Resume a generic review interrupt while yielding snapshots."""
        record, review, resume_payload = self._review_decision_context(session_id, review_id, request)

        async def stream() -> AsyncIterator[SessionResponse]:
            self._append_event(
                record,
                _generic_review_event_kind(review.kind, request.action),
                _generic_review_event_message(review, request),
                review_id=review.id,
                review_kind=review.kind,
                review_action=request.action,
            )
            _mark_review_decided(record, review, action=request.action, decision=_review_decision_payload(request))
            record.session.updated_at = _now()
            if request.action == "cancel":
                record.session.status = "cancelled"
                record.session.error = request.reason
                record.session.pending_review = None
                self._persist(record)
                yield _snapshot(record.session)
                return

            record.session.status = _pending_review_status(request.action)
            self._persist(record)
            yield _snapshot(record.session)
            payload = Command(resume=resume_payload)
            async for session in self._invoke_stream(
                record,
                payload,
                yield_initial=False,
                default_status=_review_resume_default_status(request.action),
            ):
                yield session

        return stream()

    def _plan_review_context(self, session_id: str, request: ReviewRequest) -> tuple[SessionRecord, ReviewRecord]:
        record = self._record(session_id)
        review = _pending_review(record, kind="plan_review", review_id=request.review_id)
        _ensure_action_allowed(review, request.action)
        return record, review

    def _review_decision_context(
        self,
        session_id: str,
        review_id: str,
        request: ReviewDecisionRequest,
    ) -> tuple[SessionRecord, ReviewRecord, dict[str, object]]:
        record = self._record(session_id)
        review = _pending_review(record, review_id=request.review_id or review_id)
        _ensure_review_path_matches(review, review_id)
        resume_payload = _resume_payload_for_review(review, request)
        return record, review, resume_payload

    def _record(self, session_id: str) -> SessionRecord:
        try:
            return self._sessions[session_id]
        except KeyError as exc:
            try:
                self._session_store.get(session_id)
            except SessionStoreNotFoundError as store_exc:
                raise SessionNotFoundError(session_id) from store_exc
            raise CheckpointMissingError(session_id) from exc

    def _create_record(self, request: RunRequest) -> SessionRecord:
        session_id = _uid()
        thread_id = _uid()
        base_env = FsAgentEnv.from_sources()
        model = request.model or base_env.model
        env = _env_for_model(base_env, model)
        thinking = resolve_thinking_enabled(env, requested=request.thinking)
        assembly = _runtime_assembly_for_request(request)
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
            pendingReview=None,
            reviews=[],
            toolCalls=[],
            artifacts=[],
            evidence=[],
            verification=[],
            createdAt=_now(),
            updatedAt=_now(),
        )
        saver = InMemorySaver()
        runtime = self._make_runtime(request, saver, assembly)
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
            config={
                "configurable": {"thread_id": thread_id},
                "metadata": {"tool_policy_profile": assembly.tool_policy_profile},
                "checkpoint_ref": self._checkpoint_ref or thread_id,
            },
            logger=run_logger,
        )
        self._sessions[session_id] = record
        self._session_store.create(_store_record(record))
        self._append_event(
            record,
            "session.created",
            f"创建会话 ({request.mode.upper()} 模式)",
            mcp_enabled=request.mcp_enabled,
            trust_project_mcp=request.trust_project_mcp,
            user_message_length=len(request.message),
        )
        self._append_event(record, "run.started", f"runtime 启动 · model={model}")
        for warning in assembly.warnings:
            self._append_event(
                record,
                "runtime.profile_warning",
                warning,
                profile=assembly.profile,
                backend_profile=assembly.backend_profile,
                permission_profile=assembly.permission_profile,
                tool_policy_profile=assembly.tool_policy_profile,
            )
        return record

    def _make_runtime(
        self,
        request: RunRequest,
        checkpointer: InMemorySaver,
        assembly: RuntimeAssemblyConfig,
    ) -> RuntimeLike:
        parameters = inspect.signature(self._runtime_factory).parameters
        request_only_parameter_count = 1
        request_checkpointer_parameter_count = 2
        if len(parameters) == request_only_parameter_count:
            return self._runtime_factory(request)
        if len(parameters) == request_checkpointer_parameter_count:
            return self._runtime_factory(request, checkpointer)
        return self._runtime_factory(request, checkpointer, assembly)

    def _apply_runtime_result(
        self,
        record: SessionRecord,
        result: Mapping[str, object],
        *,
        default_status: str = "completed",
    ) -> None:
        interrupt = _first_interrupt_value(result.get("__interrupt__"))
        if isinstance(interrupt, Mapping):
            review = _create_review(record, interrupt)
            if review.kind == "plan_review":
                record.session.status = "awaiting_plan_review"
                record.session.todos = _todos(interrupt.get("todos"))
                record.session.plan_meta = _plan_meta(interrupt.get("plan_meta"))
            else:
                record.session.status = "awaiting_tool_review"
            record.session.pending_review = review
            record.session.reviews.append(review)
            for event in _hitl_policy_events(record, review):
                self._apply_progress_event(record, event)
            message = str(interrupt.get("instructions") or "等待用户审核")
            self._append_event(
                record,
                f"interrupt.{review.kind}",
                message,
                review_id=review.id,
                review_kind=review.kind,
                risk=review.risk,
            )
            record.session.updated_at = _now()
            self._persist(record)
            return

        record.session.todos = _todos(result.get("todos"), fallback=record.session.todos)
        record.session.execution_log = _execution_log(
            result.get("execution_log"),
            todos=record.session.todos,
            fallback=record.session.execution_log,
        )
        record.session.tool_calls = _tool_calls(result.get("tool_calls"), fallback=record.session.tool_calls)
        record.session.execution_log = _link_execution_logs_to_tool_calls(
            record.session.execution_log,
            record.session.tool_calls,
        )
        record.session.artifacts = _artifacts(result.get("artifacts"), fallback=record.session.artifacts)
        record.session.evidence = _evidence(result.get("evidence"), fallback=record.session.evidence)
        record.session.verification = _verification(result.get("verification"), fallback=record.session.verification)
        record.session.final_response = str(result.get("final_response") or "")
        record.session.status = _result_status(result.get("status"), default=default_status)
        record.session.pending_review = None
        terminal_kind, terminal_message = _terminal_run_event(record.session.status)
        self._append_event(
            record,
            terminal_kind,
            terminal_message,
            final_response_length=len(record.session.final_response or ""),
        )
        record.session.updated_at = _now()
        self._persist(record)

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
            record.session.execution_log = _execution_log(
                event.get("execution_log"),
                todos=record.session.todos,
                fallback=record.session.execution_log,
            )
        tool_call_updates = event.get("tool_calls")
        if "tool_calls" in event or _is_agent_tool_lifecycle_event(kind):
            todo_id = _todo_id_from_event(event, record.session.todos) if _is_agent_tool_lifecycle_event(kind) else None
            record.session.tool_calls = _tool_calls(
                tool_call_updates
                if isinstance(tool_call_updates, list)
                else _tool_calls_from_lifecycle_event(event, todo_id=todo_id),
                fallback=record.session.tool_calls,
            )
            record.session.execution_log = _link_execution_logs_to_tool_calls(
                record.session.execution_log,
                record.session.tool_calls,
            )
        if "artifacts" in event:
            record.session.artifacts = _artifacts(event.get("artifacts"), fallback=record.session.artifacts)
        if "evidence" in event:
            record.session.evidence = _evidence(event.get("evidence"), fallback=record.session.evidence)
        if "verification" in event:
            record.session.verification = _verification(event.get("verification"), fallback=record.session.verification)
        self._append_event(record, kind, message, **_progress_log_fields(event))
        record.session.updated_at = _now()
        self._persist(record)

    async def _invoke_once(
        self,
        record: SessionRecord,
        payload: object,
        *,
        default_status: str = "completed",
    ) -> None:
        with self._observe_runtime_call(record, payload, operation="invoke") as observation:
            try:
                result = await record.runtime.ainvoke(payload, config=self._runtime_config(record))
            except Exception as exc:  # noqa: BLE001
                self._mark_failed(record, exc)
                return
            self._update_langfuse_observation(record, observation, result)

        self._apply_runtime_result(record, result, default_status=default_status)

    async def _invoke_stream(
        self,
        record: SessionRecord,
        payload: object,
        *,
        yield_initial: bool = True,
        default_status: str = "completed",
    ) -> AsyncIterator[SessionResponse]:
        queue: asyncio.Queue[SessionResponse | None] = asyncio.Queue()

        async def on_progress() -> None:
            await queue.put(_snapshot(record.session))

        async def run() -> None:
            with self._observe_runtime_call(record, payload, operation="stream") as observation:
                try:
                    result = await record.runtime.ainvoke(
                        payload,
                        config=self._runtime_config(record, on_progress=on_progress),
                    )
                except Exception as exc:  # noqa: BLE001
                    self._mark_failed(record, exc)
                else:
                    self._update_langfuse_observation(record, observation, result)
                    self._apply_runtime_result(record, result, default_status=default_status)
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
        self._persist(record)

    def _append_event(self, record: SessionRecord, kind: str, message: str, **fields: object) -> None:
        timeline_fields = _timeline_fields(fields)
        record.session.timeline.append(
            _event(kind, message, fields=timeline_fields, correlation_id=_timeline_correlation_id(timeline_fields))
        )
        self._persist(record)
        log_fields = {**_session_log_fields(record.session), **fields}
        exception = log_fields.pop("exception", None)
        if isinstance(exception, Exception):
            record.logger.exception(kind, message, exception, **log_fields)
        else:
            record.logger.event(kind, message, **log_fields)

    def _persist(self, record: SessionRecord) -> None:
        self._session_store.update(_store_record(record))

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

        config: dict[str, object] = {
            **record.config,
            "metadata": {
                **dict(record.config.get("metadata") or {}),
                "fsagent_event_sink": event_sink,
            },
        }
        callbacks = _callbacks(record.config.get("callbacks"))
        langfuse_callback = self._langfuse_callback_handler(record)
        if langfuse_callback is not None:
            callbacks.append(langfuse_callback)
        if callbacks:
            config["callbacks"] = callbacks
        return config

    def _langfuse_callback_handler(self, record: SessionRecord) -> object | None:
        try:
            return self._langfuse.callback_handler()
        except Exception as exc:  # noqa: BLE001
            self._log_langfuse_exception(
                record,
                "langfuse.callback_failed",
                "Langfuse callback setup failed; continuing without tracing callback.",
                exc,
            )
            return None

    @contextmanager
    def _observe_runtime_call(
        self,
        record: SessionRecord,
        payload: object,
        *,
        operation: str,
    ) -> Iterator[object | None]:
        try:
            context = self._langfuse.observe_run(
                session_id=record.session.session_id,
                thread_id=record.session.thread_id,
                mode=record.session.mode,
                model=record.session.model,
                operation=operation,
                input_payload=payload,
            )
        except Exception as exc:  # noqa: BLE001
            self._log_langfuse_exception(record, "langfuse.observe_failed", "Langfuse observation failed.", exc)
            yield None
            return

        try:
            observation = context.__enter__()
        except Exception as exc:  # noqa: BLE001
            self._log_langfuse_exception(record, "langfuse.observe_failed", "Langfuse observation failed.", exc)
            yield None
            return

        body_error: BaseException | None = None
        try:
            yield observation
        except BaseException as exc:
            body_error = exc
            raise
        finally:
            self._exit_langfuse_observation(record, context, body_error)

    def _exit_langfuse_observation(
        self,
        record: SessionRecord,
        context: AbstractContextManager[object | None],
        body_error: BaseException | None,
    ) -> None:
        try:
            if body_error is None:
                context.__exit__(None, None, None)
            else:
                context.__exit__(type(body_error), body_error, body_error.__traceback__)
        except Exception as exc:  # noqa: BLE001
            self._log_langfuse_exception(record, "langfuse.observe_failed", "Langfuse observation failed.", exc)

    def _update_langfuse_observation(
        self,
        record: SessionRecord,
        observation: object | None,
        result: Mapping[str, object],
    ) -> None:
        if observation is None:
            return
        try:
            observation.update(output=_summarize_payload_for_langfuse(result))
        except Exception as exc:  # noqa: BLE001
            self._log_langfuse_exception(record, "langfuse.update_failed", "Langfuse observation update failed.", exc)

    def _log_langfuse_exception(
        self,
        record: SessionRecord,
        event: str,
        message: str,
        error: Exception,
    ) -> None:
        record.logger.exception(event, message, error)

    @staticmethod
    def _default_runtime_factory(
        request: RunRequest,
        checkpointer: InMemorySaver,
        assembly: RuntimeAssemblyConfig,
    ) -> RuntimeLike:
        env = FsAgentEnv.from_sources()
        if request.model:
            env = _env_for_model(env, request.model)
        return create_runtime(
            model=build_chat_qwen(env, thinking=request.thinking),
            checkpointer=checkpointer,
            mcp_config_path=request.mcp_config_path if request.mcp_enabled else None,
            no_mcp=not request.mcp_enabled,
            trust_project_mcp=request.trust_project_mcp,
            tool_policy_profile=assembly.tool_policy_profile,
            permissions=assembly.permissions,
        )


def _runtime_assembly_for_request(request: RunRequest) -> RuntimeAssemblyConfig:
    return resolve_runtime_assembly(
        profile=request.profile,
        backend_profile=request.backend_profile,
        permission_profile=request.permission_profile,
        tool_policy_profile=request.tool_policy_profile,
    )


def _env_for_model(env: FsAgentEnv, model: str) -> FsAgentEnv:
    return FsAgentEnv(
        model=model,
        base_url=env.base_url,
        api_key=env.api_key,
        available_models_json=env.available_models_json,
        env_dir=env.env_dir,
    )


def _store_record(record: SessionRecord) -> SessionStoreRecord:
    configurable = record.config.get("configurable")
    runtime_config = dict(configurable) if isinstance(configurable, Mapping) else {}
    checkpoint_ref = record.config.get("checkpoint_ref")
    return SessionStoreRecord(
        session=record.session,
        runtime_config=runtime_config,
        checkpoint_ref=str(checkpoint_ref or runtime_config.get("thread_id") or ""),
    )


def _first_interrupt_value(value: object) -> object | None:
    interrupt = value
    if isinstance(value, list | tuple):
        if not value:
            return None
        interrupt = value[0]
    return getattr(interrupt, "value", interrupt)


def _create_review(record: SessionRecord, interrupt: Mapping[str, object]) -> ReviewRecord:
    kind = _review_kind(interrupt.get("kind"))
    now = _now()
    default_review_id = f"review-{len(record.session.reviews) + 1:03d}"
    review_id = interrupt.get("review_id") or interrupt.get("reviewId") or default_review_id
    action_requests = _hitl_action_requests(interrupt)
    review_configs = _hitl_review_configs(interrupt)
    allowed_actions = (
        interrupt.get("allowed_actions") or interrupt.get("allowedActions") or _hitl_allowed_actions(review_configs)
    )
    raw_risk = interrupt.get("risk")
    return ReviewRecord(
        id=str(review_id),
        kind=kind,
        status="pending",
        risk=_hitl_risk(action_requests) if raw_risk is None and action_requests else _risk(raw_risk),
        subject=_review_subject(kind, interrupt, record),
        proposedInputSummary=_proposed_input_summary(interrupt),
        allowedActions=_allowed_review_actions(kind, allowed_actions),
        decision=None,
        createdAt=now,
        updatedAt=now,
        expiresAt=interrupt.get("expires_at") or interrupt.get("expiresAt"),
    )


def _pending_review(
    record: SessionRecord,
    *,
    kind: str | None = None,
    review_id: str | None = None,
) -> ReviewRecord:
    review = record.session.pending_review
    if review is None or review.status != "pending":
        msg = "No pending review."
        raise ReviewNotFoundError(msg)
    if kind is not None and review.kind != kind:
        msg = f"Pending review is {review.kind}, not {kind}."
        raise ReviewConflictError(msg)
    if review_id is not None and review.id != review_id:
        msg = "Review id does not match the pending review."
        raise ReviewConflictError(msg)
    return review


def _ensure_review_path_matches(review: ReviewRecord, review_id: str) -> None:
    if review.id != review_id:
        msg = "Review id does not match the pending review."
        raise ReviewConflictError(msg)


def _ensure_action_allowed(review: ReviewRecord, action: str) -> None:
    if review.allowed_actions and action not in review.allowed_actions:
        msg = f"Action {action} is not allowed for review {review.id}."
        raise ReviewConflictError(msg)


def _mark_review_decided(
    record: SessionRecord,
    review: ReviewRecord,
    *,
    action: str,
    decision: dict[str, object],
) -> None:
    review.status = _review_status_for_action(action)
    review.decision = decision
    review.updated_at = _now()
    record.session.pending_review = None


def _review_kind(value: object) -> str:
    kind = str(value or "tool_review")
    if kind not in {"plan_review", "tool_review", "deviation_review", "mcp_review"}:
        return "tool_review"
    return kind


def _risk(value: object) -> str:
    risk = str(value or "low")
    if risk not in {"low", "medium", "high", "critical"}:
        return "low"
    return risk


def _allowed_review_actions(kind: str, value: object) -> list[str]:
    if isinstance(value, list) and value:
        actions: list[str] = []
        for item in value:
            action = _api_review_action(kind, str(item))
            if action is not None and action not in actions:
                actions.append(action)
        if kind != "plan_review" and "cancel" not in actions:
            actions.append("cancel")
        return actions
    return {
        "plan_review": ["approve", "edit", "retry", "cancel"],
        "tool_review": ["approve", "modify", "deny", "cancel"],
        "deviation_review": ["approve", "replan", "cancel"],
        "mcp_review": ["approve", "deny", "cancel"],
    }[kind]


def _api_review_action(kind: str, action: str) -> str | None:
    if action == "reject":
        return "deny"
    if action == "edit" and kind != "plan_review":
        return "modify"
    if action == "respond":
        return "respond" if kind != "plan_review" else None
    if action in {"approve", "edit", "retry", "cancel", "modify", "deny", "replan", "respond"}:
        return action
    return None


def _hitl_action_requests(interrupt: Mapping[str, object]) -> list[dict[str, object]]:
    value = interrupt.get("action_requests") or interrupt.get("actionRequests")
    if not isinstance(value, list):
        return []
    action_requests: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        args = item.get("args")
        action_request: dict[str, object] = {
            "name": str(item.get("name") or ""),
            "args": dict(args) if isinstance(args, Mapping) else {},
        }
        tool_call_id = item.get("id") or item.get("tool_call_id") or item.get("toolCallId")
        if tool_call_id is not None:
            action_request["id"] = str(tool_call_id)
        description = item.get("description")
        if description is not None:
            action_request["description"] = str(description)
        action_requests.append(action_request)
    return action_requests


def _hitl_review_configs(interrupt: Mapping[str, object]) -> list[dict[str, object]]:
    value = interrupt.get("review_configs") or interrupt.get("reviewConfigs")
    if not isinstance(value, list):
        return []
    review_configs: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        allowed_decisions = item.get("allowed_decisions") or item.get("allowedDecisions") or []
        review_configs.append(
            {
                "actionName": str(item.get("action_name") or item.get("actionName") or ""),
                "allowedDecisions": _string_list(allowed_decisions),
            }
        )
    return review_configs


def _hitl_allowed_actions(review_configs: list[dict[str, object]]) -> list[str]:
    actions: list[str] = []
    for config in review_configs:
        for decision in _string_list(config.get("allowedDecisions")):
            if decision not in actions:
                actions.append(decision)
    return actions


def _hitl_risk(action_requests: list[dict[str, object]]) -> str:
    if any(_hitl_action_risk(item) == "high" for item in action_requests):
        return "high"
    return "medium"


def _hitl_action_risk(action_request: Mapping[str, object]) -> str:
    return "high" if is_high_risk_tool_text(action_request.get("name"), action_request.get("description")) else "medium"


def _hitl_action_summary(action_request: Mapping[str, object]) -> str:
    name = str(action_request.get("name") or "tool")
    args = action_request.get("args")
    try:
        args_summary = json.dumps(args if isinstance(args, Mapping) else {}, ensure_ascii=False, default=str)[:300]
    except TypeError:
        args_summary = str(args)[:300]
    return f"{name}: {args_summary}"


def _review_subject(kind: str, interrupt: Mapping[str, object], record: SessionRecord) -> dict[str, object]:
    action_requests = _hitl_action_requests(interrupt)
    if kind == "tool_review" and action_requests:
        return {
            "actionRequests": action_requests,
            "reviewConfigs": _hitl_review_configs(interrupt),
        }
    subject = interrupt.get("subject")
    if isinstance(subject, Mapping):
        return {str(key): value for key, value in subject.items()}
    if kind == "plan_review":
        todos = _todos(interrupt.get("todos"), fallback=record.session.todos)
        plan_meta = _plan_meta(interrupt.get("plan_meta")) or record.session.plan_meta
        return {
            "todos": [todo.model_dump(by_alias=True) for todo in todos],
            "planMeta": plan_meta.model_dump(by_alias=True) if plan_meta is not None else None,
        }
    return {}


def _proposed_input_summary(interrupt: Mapping[str, object]) -> str | None:
    summary = interrupt.get("proposed_input_summary") or interrupt.get("proposedInputSummary")
    if summary is not None:
        return str(summary)
    action_requests = _hitl_action_requests(interrupt)
    if action_requests:
        return "; ".join(_hitl_action_summary(item) for item in action_requests)
    instructions = interrupt.get("instructions")
    return str(instructions) if instructions is not None else None


def _hitl_policy_events(record: SessionRecord, review: ReviewRecord) -> list[dict[str, object]]:
    if review.kind != "tool_review":
        return []
    action_requests = review.subject.get("actionRequests")
    if not isinstance(action_requests, list):
        return []
    profile = _record_tool_policy_profile(record)
    phase = "fast_runner" if record.session.mode == "fast" else "executor"
    events: list[dict[str, object]] = []
    for index, action_request in enumerate(action_requests, start=1):
        if not isinstance(action_request, Mapping):
            continue
        tool_name = str(action_request.get("name") or "")
        decision = evaluate_tool_policy(tool_name, profile=profile, phase=phase)
        risk = _hitl_action_risk(action_request)
        if decision.action != "review" and risk != "high":
            continue
        tool_call_id = _hitl_tool_call_id(review, action_request, index)
        events.append(
            {
                "kind": "tool.policy_decision",
                "message": f"Tool policy review: {tool_name}",
                "tool_name": tool_name,
                "tool_call_id": tool_call_id,
                "review_id": review.id,
                "risk": risk,
                "profile": decision.profile,
                "policyDecision": "review",
                "reason": decision.reason if decision.action == "review" else "Tool call requires review.",
                "tool_calls": [
                    {
                        "id": tool_call_id,
                        "name": tool_name,
                        "risk": risk,
                        "status": "review_required",
                        "inputSummary": _hitl_action_summary(action_request),
                        "reviewId": review.id,
                    }
                ],
            }
        )
    return events


def _record_tool_policy_profile(record: SessionRecord) -> str:
    metadata = record.config.get("metadata")
    if isinstance(metadata, Mapping):
        return str(metadata.get("tool_policy_profile") or "dev-default")
    return "dev-default"


def _hitl_tool_call_id(review: ReviewRecord, action_request: Mapping[str, object], index: int) -> str:
    for key in ("id", "tool_call_id", "toolCallId"):
        value = action_request.get(key)
        if value:
            return str(value)
    return f"{review.id}-tool-{index:03d}"


def _review_status_for_action(action: str) -> str:
    if action in {"approve", "respond"}:
        return "approved"
    if action in {"edit", "retry", "modify", "replan"}:
        return "modified"
    if action == "deny":
        return "denied"
    return "cancelled"


def _review_request_decision(request: ReviewRequest) -> dict[str, object]:
    decision = request.model_dump(by_alias=True, exclude_none=True)
    return {str(key): value for key, value in decision.items()}


def _review_decision_payload(request: ReviewDecisionRequest) -> dict[str, object]:
    decision = request.model_dump(by_alias=True, exclude_none=True)
    return {str(key): value for key, value in decision.items()}


def _resume_payload_for_review(review: ReviewRecord, request: ReviewDecisionRequest) -> dict[str, object]:
    _ensure_action_allowed(review, request.action)
    if request.action == "cancel":
        return {}
    if review.kind == "plan_review":
        return _plan_resume_payload(request)
    if review.kind in {"mcp_review", "deviation_review"}:
        return _gate_resume_payload(request)
    return {"decisions": _tool_resume_decisions(review, request)}


def _gate_resume_payload(request: ReviewDecisionRequest) -> dict[str, object]:
    payload: dict[str, object] = {"action": request.action}
    if request.reason:
        payload["reason"] = request.reason
    if request.feedback:
        payload["feedback"] = request.feedback
    if request.edited_subject is not None:
        payload["editedSubject"] = request.edited_subject
    return payload


def _plan_resume_payload(request: ReviewDecisionRequest) -> dict[str, object]:
    if request.action not in {"approve", "edit", "retry", "cancel"}:
        msg = f"Invalid plan review action: {request.action}"
        raise ReviewConflictError(msg)
    payload: dict[str, object] = {"action": request.action}
    subject = request.edited_subject or {}
    if request.action == "edit":
        if "todos" in subject:
            payload["todos"] = subject["todos"]
        if "planMeta" in subject:
            payload["plan_meta"] = subject["planMeta"]
        elif "plan_meta" in subject:
            payload["plan_meta"] = subject["plan_meta"]
    if request.feedback:
        payload["feedback"] = request.feedback
    if request.reason:
        payload["reason"] = request.reason
    return payload


def _tool_resume_decisions(review: ReviewRecord, request: ReviewDecisionRequest) -> list[dict[str, object]]:
    action_count = _tool_review_action_count(review)
    if request.action == "approve":
        _ensure_tool_decision_allowed(review, "approve", action_count)
        return [{"type": "approve"} for _ in range(action_count)]
    if request.action in {"deny", "replan", "retry"}:
        _ensure_tool_decision_allowed(review, "reject", action_count)
        return [_tool_reject_decision(request) for _ in range(action_count)]
    if request.action == "respond":
        return _tool_respond_decisions(review, request, action_count)
    if request.action not in {"modify", "edit"}:
        msg = f"Invalid tool review action: {request.action}"
        raise ReviewConflictError(msg)
    if request.edited_subject is None:
        msg = "Edited subject is required for modified tool reviews."
        raise ReviewConflictError(msg)
    decisions = request.edited_subject.get("decisions")
    if isinstance(decisions, list):
        return _normalized_tool_decision_list(review, decisions, action_count)
    if action_count != 1:
        msg = "Edited subject must provide decisions for all reviewed tool actions."
        raise ReviewConflictError(msg)
    return [_tool_edit_decision(review, request)]


def _tool_reject_decision(request: ReviewDecisionRequest) -> dict[str, object]:
    decision: dict[str, object] = {"type": "reject"}
    message = request.feedback or request.reason
    if message:
        decision["message"] = message
    return decision


def _tool_respond_decisions(
    review: ReviewRecord,
    request: ReviewDecisionRequest,
    action_count: int,
) -> list[dict[str, object]]:
    message = request.feedback or request.reason
    if not message:
        msg = "Respond tool review requires feedback or reason."
        raise ReviewConflictError(msg)
    _ensure_tool_decision_allowed(review, "respond", action_count)
    return [{"type": "respond", "message": message} for _ in range(action_count)]


def _tool_edit_decision(review: ReviewRecord, request: ReviewDecisionRequest) -> dict[str, object]:
    subject = request.edited_subject or {}
    edited_action_input = subject.get("edited_action") or subject.get("editedAction") or subject
    edited_action = _normalized_edited_tool_action(review, 0, edited_action_input)
    decision: dict[str, object] = {"type": "edit", "edited_action": edited_action}
    message = request.feedback or request.reason
    if message:
        decision["message"] = message
    return decision


def _tool_review_action_count(review: ReviewRecord) -> int:
    action_requests = review.subject.get("actionRequests")
    if isinstance(action_requests, list) and action_requests:
        return len(action_requests)
    return 1


def _normalized_tool_decision_list(
    review: ReviewRecord,
    decisions: list[object],
    action_count: int,
) -> list[dict[str, object]]:
    if len(decisions) != action_count:
        msg = "Edited subject must provide decisions for all reviewed tool actions."
        raise ReviewConflictError(msg)
    allowed_by_index = _tool_review_allowed_decisions(review, action_count)
    return [
        _normalized_tool_decision(review, index, decision, allowed_by_index[index])
        for index, decision in enumerate(decisions)
    ]


def _normalized_tool_decision(
    review: ReviewRecord,
    index: int,
    decision: object,
    allowed_decisions: set[str],
) -> dict[str, object]:
    if not isinstance(decision, Mapping):
        msg = "Edited subject decisions must be objects."
        raise ReviewConflictError(msg)
    decision_type = str(decision.get("type") or "")
    if decision_type not in {"approve", "edit", "reject", "respond"}:
        msg = "Tool decision type must be approve, edit, reject, or respond."
        raise ReviewConflictError(msg)
    if decision_type not in allowed_decisions:
        msg = f"Decision type {decision_type} is not allowed for reviewed tool action {index + 1}."
        raise ReviewConflictError(msg)
    normalized: dict[str, object] = {"type": decision_type}
    if decision_type == "edit":
        edited_action = decision.get("edited_action") or decision.get("editedAction")
        normalized["edited_action"] = _normalized_edited_tool_action(review, index, edited_action)
    elif decision_type in {"reject", "respond"}:
        message = decision.get("message")
        if decision_type == "respond" and not message:
            msg = "Respond tool decision must include message."
            raise ReviewConflictError(msg)
        if message is not None:
            normalized["message"] = str(message)
    return normalized


def _ensure_tool_decision_allowed(review: ReviewRecord, decision_type: str, action_count: int) -> None:
    for index, allowed_decisions in enumerate(_tool_review_allowed_decisions(review, action_count), start=1):
        if decision_type not in allowed_decisions:
            msg = f"Decision type {decision_type} is not allowed for reviewed tool action {index}."
            raise ReviewConflictError(msg)


def _normalized_edited_tool_action(review: ReviewRecord, index: int, value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        msg = "Edited tool decision must include edited_action."
        raise ReviewConflictError(msg)
    edited_action = {str(key): item for key, item in value.items()}
    if "name" not in edited_action:
        name = _tool_action_name(review, index)
        if name is not None:
            edited_action["name"] = name
    if not edited_action.get("name"):
        msg = "Edited tool decision must include edited_action name."
        raise ReviewConflictError(msg)
    args = edited_action.get("args")
    if not isinstance(args, Mapping):
        msg = "Edited tool decision must include edited_action args."
        raise ReviewConflictError(msg)
    edited_action["args"] = {str(key): item for key, item in args.items()}
    return edited_action


def _tool_review_allowed_decisions(review: ReviewRecord, action_count: int) -> list[set[str]]:
    configs = review.subject.get("reviewConfigs")
    allowed: list[set[str]] = []
    for index in range(action_count):
        config = configs[index] if isinstance(configs, list) and index < len(configs) else None
        decisions = set(_string_list(config.get("allowedDecisions"))) if isinstance(config, Mapping) else set()
        allowed.append(decisions or {"approve", "edit", "reject"})
    return allowed


def _first_tool_action_name(review: ReviewRecord) -> str | None:
    return _tool_action_name(review, 0)


def _tool_action_name(review: ReviewRecord, index: int) -> str | None:
    action_requests = review.subject.get("actionRequests")
    if not isinstance(action_requests, list) or not action_requests:
        return None
    if index >= len(action_requests):
        return None
    action_request = action_requests[index]
    if not isinstance(action_request, Mapping):
        return None
    name = action_request.get("name")
    return str(name) if name is not None else None


def _todos(value: object, *, fallback: list[TodoItem] | None = None) -> list[TodoItem]:
    if not isinstance(value, list):
        return fallback or []
    todos = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, Mapping):
            continue
        status = str(item.get("status") or "pending")
        if status not in {"pending", "in_progress", "completed", "failed", "blocked"}:
            status = "pending"
        risk = str(item.get("risk") or "low")
        if risk not in {"low", "medium", "high", "critical"}:
            risk = "low"
        todos.append(
            TodoItem(
                id=str(item.get("id") or f"todo-{index:03d}"),
                content=str(item.get("content") or ""),
                status=status,
                risk=risk,
                dependsOn=_string_list(item.get("depends_on") or item.get("dependsOn")),
                evidenceIds=_string_list(item.get("evidence_ids") or item.get("evidenceIds")),
                verificationIds=_string_list(item.get("verification_ids") or item.get("verificationIds")),
                failureReason=item.get("failure_reason") or item.get("failureReason"),
            )
        )
    return todos


def _plan_meta(value: object) -> PlanMeta | None:
    if not isinstance(value, Mapping):
        return None
    verification = value.get("verification")
    return PlanMeta(
        goal=str(value.get("goal") or ""),
        assumptions=[str(item) for item in value.get("assumptions") or []],
        final_output_format=value.get("final_output_format") or value.get("finalOutputFormat"),
        verification=[str(item).strip() for item in verification if str(item).strip()]
        if isinstance(verification, list)
        else [],
    )


def _execution_log(
    value: object,
    *,
    todos: list[TodoItem] | None = None,
    fallback: list[ExecutionLogEntry] | None = None,
) -> list[ExecutionLogEntry]:
    if not isinstance(value, list):
        return fallback or []
    entries = []
    todo_id_by_content = _todo_id_by_unique_content(todos or [])
    for index, item in enumerate(value, start=1):
        if not isinstance(item, Mapping):
            continue
        status = str(item.get("status") or "pending")
        if status not in {"pending", "in_progress", "completed", "skipped", "failed", "blocked"}:
            status = "pending"
        content = str(item.get("content") or "")
        entries.append(
            ExecutionLogEntry(
                id=str(item.get("id") or f"log-{index:03d}"),
                todoId=str(
                    item.get("todo_id") or item.get("todoId") or todo_id_by_content.get(content) or f"todo-{index:03d}"
                ),
                content=content,
                status=status,
                result=item.get("result"),
                error=item.get("error"),
                startedAt=item.get("started_at") or item.get("startedAt"),
                completedAt=item.get("completed_at") or item.get("completedAt"),
                toolCallIds=_string_list(item.get("tool_call_ids") or item.get("toolCallIds")),
                artifactIds=_string_list(item.get("artifact_ids") or item.get("artifactIds")),
                verificationIds=_string_list(item.get("verification_ids") or item.get("verificationIds")),
            )
        )
    return entries


def _todo_id_from_event(event: Mapping[str, object], todos: list[TodoItem]) -> str | None:
    index_value = event.get("todo_index") or event.get("todoIndex")
    try:
        todo_index = int(index_value) if index_value is not None else None
    except (TypeError, ValueError):
        todo_index = None
    if todo_index is not None and 0 < todo_index <= len(todos):
        return todos[todo_index - 1].id

    content = event.get("todo_content") or event.get("todoContent")
    if content is not None:
        return _todo_id_by_unique_content(todos).get(str(content))
    return None


def _todo_id_by_unique_content(todos: list[TodoItem]) -> dict[str, str]:
    todo_ids_by_content: dict[str, list[str]] = {}
    for todo in todos:
        if todo.id is None:
            continue
        todo_ids_by_content.setdefault(todo.content, []).append(todo.id)
    return {content: todo_ids[0] for content, todo_ids in todo_ids_by_content.items() if len(todo_ids) == 1}


def _link_execution_logs_to_tool_calls(
    entries: list[ExecutionLogEntry],
    tool_calls: list[ToolCallRecord],
) -> list[ExecutionLogEntry]:
    tool_call_ids_by_todo: dict[str, list[str]] = {}
    for tool_call in tool_calls:
        if tool_call.todo_id is None:
            continue
        tool_call_ids = tool_call_ids_by_todo.setdefault(tool_call.todo_id, [])
        if tool_call.id not in tool_call_ids:
            tool_call_ids.append(tool_call.id)

    linked_entries: list[ExecutionLogEntry] = []
    for entry in entries:
        if entry.todo_id is None or entry.tool_call_ids:
            linked_entries.append(entry)
            continue
        tool_call_ids = tool_call_ids_by_todo.get(entry.todo_id)
        if not tool_call_ids:
            linked_entries.append(entry)
            continue
        linked_entries.append(
            ExecutionLogEntry(
                id=entry.id,
                todoId=entry.todo_id,
                content=entry.content,
                status=entry.status,
                result=entry.result,
                error=entry.error,
                startedAt=entry.started_at,
                completedAt=entry.completed_at,
                toolCallIds=tool_call_ids,
                artifactIds=entry.artifact_ids,
                verificationIds=entry.verification_ids,
            )
        )
    return linked_entries


def _result_status(value: object, *, default: str = "completed") -> str:
    status = str(default) if value is None else str(value)
    if status in {
        "idle",
        "running",
        "planning",
        "awaiting_plan_review",
        "editing_plan",
        "retrying_plan",
        "executing",
        "awaiting_tool_review",
        "verifying",
        "needs_revision",
        "completed",
        "cancelled",
        "failed",
    }:
        return status
    return "completed"


def _tool_calls(value: object, *, fallback: list[ToolCallRecord] | None = None) -> list[ToolCallRecord]:
    if not isinstance(value, list):
        return fallback or []
    records: list[tuple[ToolCallRecord, set[str]]] = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, Mapping):
            continue
        present_fields = _tool_call_present_fields(item)
        risk = str(item.get("risk") or "low")
        if risk not in {"low", "medium", "high", "critical"}:
            risk = "low"
        records.append(
            (
                ToolCallRecord(
                    id=str(item.get("id") or f"tool-{index:03d}"),
                    todoId=item.get("todo_id") or item.get("todoId"),
                    name=str(item.get("name") or ""),
                    risk=risk,
                    status=str(item.get("status") or "completed"),
                    inputSummary=item.get("input_summary") or item.get("inputSummary"),
                    outputSummary=item.get("output_summary") or item.get("outputSummary"),
                    reviewId=item.get("review_id") or item.get("reviewId"),
                    durationMs=item.get("duration_ms") or item.get("durationMs"),
                ),
                present_fields,
            )
        )
    if not fallback:
        return [record for record, _present_fields in records]
    return _merge_tool_calls(fallback, records)


def _merge_tool_calls(
    existing: list[ToolCallRecord],
    updates: list[tuple[ToolCallRecord, set[str]]],
) -> list[ToolCallRecord]:
    merged = {record.id: record for record in existing}
    order = [record.id for record in existing]
    for record, present_fields in updates:
        if record.id not in merged:
            order.append(record.id)
        merged[record.id] = _merge_tool_call(merged.get(record.id), record, present_fields)
    return [merged[record_id] for record_id in order]


def _merge_tool_call(
    existing: ToolCallRecord | None,
    update: ToolCallRecord,
    present_fields: set[str],
) -> ToolCallRecord:
    if existing is None:
        return update
    return ToolCallRecord(
        id=update.id,
        todoId=update.todo_id if "todo_id" in present_fields else existing.todo_id,
        name=update.name if "name" in present_fields else existing.name,
        risk=update.risk if "risk" in present_fields else existing.risk,
        status=update.status if "status" in present_fields else existing.status,
        inputSummary=update.input_summary if "input_summary" in present_fields else existing.input_summary,
        outputSummary=update.output_summary if "output_summary" in present_fields else existing.output_summary,
        reviewId=update.review_id if "review_id" in present_fields else existing.review_id,
        durationMs=update.duration_ms if update.duration_ms is not None else existing.duration_ms,
    )


def _tool_call_present_fields(item: Mapping[str, object]) -> set[str]:
    fields: set[str] = set()
    if "todo_id" in item or "todoId" in item:
        fields.add("todo_id")
    if "name" in item:
        fields.add("name")
    if "risk" in item:
        fields.add("risk")
    if "status" in item:
        fields.add("status")
    if "input_summary" in item or "inputSummary" in item:
        fields.add("input_summary")
    if "output_summary" in item or "outputSummary" in item:
        fields.add("output_summary")
    if "review_id" in item or "reviewId" in item:
        fields.add("review_id")
    if "duration_ms" in item or "durationMs" in item:
        fields.add("duration_ms")
    return fields


def _is_agent_tool_lifecycle_event(kind: str) -> bool:
    return kind in {"agent.tool.started", "agent.tool.completed", "agent.tool.failed"}


def _tool_calls_from_lifecycle_event(
    event: Mapping[str, object],
    *,
    todo_id: str | None = None,
) -> list[dict[str, object]]:
    tool_name = str(event.get("tool_name") or "")
    tool_call_id = str(event.get("tool_call_id") or "")
    if not tool_name and not tool_call_id:
        return []
    kind = str(event.get("kind") or "")
    record: dict[str, object] = {
        "id": tool_call_id or f"tool-{tool_name}",
        "name": tool_name,
        "status": _tool_lifecycle_status(kind),
    }
    if todo_id is not None:
        record["todoId"] = todo_id
    duration_ms = event.get("duration_ms") or event.get("durationMs")
    if duration_ms is not None:
        record["durationMs"] = duration_ms
    output_summary = _tool_lifecycle_output_summary(event)
    if output_summary is not None:
        record["outputSummary"] = output_summary
    return [record]


def _tool_lifecycle_status(kind: str) -> str:
    return {
        "agent.tool.started": "running",
        "agent.tool.completed": "completed",
        "agent.tool.failed": "failed",
    }.get(kind, "completed")


def _tool_lifecycle_output_summary(event: Mapping[str, object]) -> str | None:
    error = event.get("error")
    if error is not None:
        return str(error)[:500]
    result_size = event.get("result_size_chars") or event.get("resultSizeChars")
    if result_size is not None:
        return f"result_size_chars={result_size}"
    return None


def _artifacts(value: object, *, fallback: list[ArtifactRecord] | None = None) -> list[ArtifactRecord]:
    if not isinstance(value, list):
        return fallback or []
    records = []
    for index, item in enumerate(value, start=1):
        if isinstance(item, str):
            records.append(
                ArtifactRecord(
                    id=f"artifact-{index:03d}",
                    kind="legacy",
                    summary=item,
                )
            )
            continue
        if not isinstance(item, Mapping):
            continue
        records.append(
            ArtifactRecord(
                id=str(item.get("id") or f"artifact-{index:03d}"),
                kind=str(item.get("kind") or "runtime"),
                path=item.get("path"),
                summary=str(item.get("summary") or item.get("path") or ""),
                size=item.get("size"),
                sha256=item.get("sha256"),
                redactionStatus=str(item.get("redaction_status") or item.get("redactionStatus") or "none"),
                createdAt=item.get("created_at") or item.get("createdAt"),
            )
        )
    return records


def _evidence(value: object, *, fallback: list[EvidenceRecord] | None = None) -> list[EvidenceRecord]:
    if not isinstance(value, list):
        return fallback or []
    records = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, Mapping):
            continue
        source = str(item.get("source") or "runtime")
        if source not in {"tool", "runtime", "verifier", "user"}:
            source = "runtime"
        records.append(
            EvidenceRecord(
                id=str(item.get("id") or f"evidence-{index:03d}"),
                todoId=item.get("todo_id") or item.get("todoId"),
                toolCallId=item.get("tool_call_id") or item.get("toolCallId"),
                artifactId=item.get("artifact_id") or item.get("artifactId"),
                summary=str(item.get("summary") or ""),
                source=source,
                createdAt=item.get("created_at") or item.get("createdAt"),
            )
        )
    return records


def _verification(value: object, *, fallback: list[VerificationRecord] | None = None) -> list[VerificationRecord]:
    if not isinstance(value, list):
        return fallback or []
    records = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, Mapping):
            continue
        status = str(item.get("status") or "skipped")
        if status not in {"passed", "failed", "skipped", "manual"}:
            status = "skipped"
        records.append(
            VerificationRecord(
                id=str(item.get("id") or f"verification-{index:03d}"),
                todoId=item.get("todo_id") or item.get("todoId"),
                command=item.get("command"),
                status=status,
                exitCode=item.get("exit_code") or item.get("exitCode"),
                stdoutSummary=item.get("stdout_summary") or item.get("stdoutSummary"),
                stderrSummary=item.get("stderr_summary") or item.get("stderrSummary"),
                artifactId=item.get("artifact_id") or item.get("artifactId"),
                reason=item.get("reason"),
                createdAt=item.get("created_at") or item.get("createdAt"),
            )
        )
    return records


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


def _generic_review_event_kind(kind: str, action: str) -> str:
    return f"{kind}.{action}"


def _generic_review_event_message(review: ReviewRecord, request: ReviewDecisionRequest) -> str:
    if request.action == "cancel":
        return f"用户取消 {review.kind}{f': {request.reason}' if request.reason else ''}"
    if request.action == "deny":
        return f"用户拒绝 {review.kind}{f': {request.reason}' if request.reason else ''}"
    if request.action == "respond":
        return f"用户回应 {review.kind}{f': {request.feedback}' if request.feedback else ''}"
    if request.action in {"edit", "modify"}:
        return f"用户修改 {review.kind}"
    if request.action in {"retry", "replan"}:
        return f"用户请求重新处理 {review.kind}{f': {request.feedback}' if request.feedback else ''}"
    return f"用户批准 {review.kind}"


def _pending_review_status(action: str) -> str:
    return {
        "approve": "executing",
        "edit": "editing_plan",
        "retry": "retrying_plan",
        "modify": "executing",
        "deny": "needs_revision",
        "replan": "retrying_plan",
        "respond": "executing",
    }.get(action, "awaiting_plan_review")


def _review_resume_default_status(action: str) -> str:
    if action == "deny":
        return "needs_revision"
    return "completed"


def _terminal_run_event(status: str) -> tuple[str, str]:
    if status == "needs_revision":
        return "run.needs_revision", "运行需要修订"
    if status == "failed":
        return "run.failed", "运行失败"
    if status == "cancelled":
        return "run.cancelled", "运行已取消"
    return "run.completed", "运行完成"


def _snapshot(session: SessionResponse) -> SessionResponse:
    return session.model_copy(deep=True)


def _session_log_fields(session: SessionResponse) -> dict[str, object]:
    return {
        "status": session.status,
        "todo_count": len(session.todos),
        "execution_log_count": len(session.execution_log),
        "timeline_count": len(session.timeline),
    }


def _event(
    kind: str,
    message: str,
    *,
    fields: dict[str, object] | None = None,
    correlation_id: str | None = None,
) -> TimelineEvent:
    return TimelineEvent(
        id=_uid(),
        kind=kind,
        message=message,
        at=_now(),
        phase=_event_phase(kind),
        severity=_event_severity(kind),
        correlationId=correlation_id,
        fields=fields or {},
    )


def _progress_log_fields(event: Mapping[str, object]) -> dict[str, object]:
    excluded = {
        "kind",
        "message",
        "todos",
        "execution_log",
        "plan_meta",
        "tool_calls",
        "artifacts",
        "evidence",
        "verification",
    }
    return {str(key): value for key, value in event.items() if key not in excluded}


def _callbacks(value: object) -> list[object]:
    if value is None:
        return []
    if isinstance(value, list | tuple):
        return list(value)
    return [value]


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _timeline_fields(fields: Mapping[str, object]) -> dict[str, object]:
    return {str(key): value for key, value in fields.items() if key != "exception"}


def _timeline_correlation_id(fields: Mapping[str, object]) -> str | None:
    for key in ("correlation_id", "correlationId", "review_id", "reviewId", "tool_call_id", "toolCallId"):
        value = fields.get(key)
        if value:
            return str(value)
    return None


def _event_phase(kind: str) -> str:
    prefix = kind.split(".", maxsplit=1)[0]
    return {
        "session": "session",
        "run": "runtime",
        "planner": "planning",
        "plan": "review",
        "interrupt": "review",
        "todo": "execution",
        "agent": "agent",
        "tool": "tool",
        "verification": "verification",
    }.get(prefix, prefix)


def _event_severity(kind: str) -> str:
    if "warning" in kind:
        return "warning"
    if kind.endswith(".needs_revision"):
        return "warning"
    if kind.endswith((".failed", ".denied")):
        return "error"
    return "info"


def _uid() -> str:
    return uuid4().hex[:12]


def _now() -> str:
    return datetime.now(UTC).isoformat()
