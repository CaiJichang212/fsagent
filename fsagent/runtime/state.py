"""Typed state for the fast/plan runtime."""

from typing import Literal, NotRequired, TypedDict

from langchain.agents.middleware.todo import PlanningState

RuntimeMode = Literal["fast", "plan"]
RiskLevel = Literal["low", "medium", "high", "critical"]
TodoStatus = Literal["pending", "in_progress", "completed", "failed", "blocked"]
ExecutionStatus = Literal["pending", "in_progress", "completed", "skipped", "blocked", "failed"]
PlanReviewAction = Literal["approve", "edit", "retry", "cancel"]
VerificationStatus = Literal["passed", "failed", "skipped", "manual"]


class TodoItem(TypedDict):
    """Todo item compatible with `TodoListMiddleware`."""

    content: str
    status: TodoStatus
    id: NotRequired[str]
    risk: NotRequired[RiskLevel]
    depends_on: NotRequired[list[str]]
    evidence_ids: NotRequired[list[str]]
    verification_ids: NotRequired[list[str]]
    failure_reason: NotRequired[str | None]


class PlanMeta(TypedDict):
    """Plan metadata shown during plan review."""

    goal: str
    assumptions: NotRequired[list[str]]
    final_output_format: NotRequired[str]


class ExecutionLogEntry(TypedDict):
    """Detailed execution result for one todo item."""

    content: str
    status: ExecutionStatus
    id: NotRequired[str]
    todo_id: NotRequired[str]
    result: NotRequired[str | None]
    error: NotRequired[str | None]
    started_at: NotRequired[str | None]
    completed_at: NotRequired[str | None]
    tool_call_ids: NotRequired[list[str]]
    artifact_ids: NotRequired[list[str]]
    verification_ids: NotRequired[list[str]]


class ToolCallRecord(TypedDict):
    """Tool lifecycle record for policy, audit, and UI display."""

    id: str
    name: str
    risk: RiskLevel
    status: str
    todo_id: NotRequired[str | None]
    input_summary: NotRequired[str | None]
    output_summary: NotRequired[str | None]
    review_id: NotRequired[str | None]
    duration_ms: NotRequired[float | None]


class ArtifactRecord(TypedDict):
    """Artifact reference captured during execution or verification."""

    id: str
    kind: str
    summary: str
    path: NotRequired[str | None]
    size: NotRequired[int | None]
    sha256: NotRequired[str | None]
    redaction_status: NotRequired[str]
    created_at: NotRequired[str | None]


class EvidenceRecord(TypedDict):
    """Evidence linked to a todo, tool call, artifact, or verifier."""

    id: str
    summary: str
    source: str
    todo_id: NotRequired[str | None]
    tool_call_id: NotRequired[str | None]
    artifact_id: NotRequired[str | None]
    created_at: NotRequired[str | None]


class VerificationRecord(TypedDict):
    """Verification outcome captured before completion."""

    id: str
    status: VerificationStatus
    todo_id: NotRequired[str | None]
    command: NotRequired[str | None]
    exit_code: NotRequired[int | None]
    stdout_summary: NotRequired[str | None]
    stderr_summary: NotRequired[str | None]
    artifact_id: NotRequired[str | None]
    reason: NotRequired[str | None]
    created_at: NotRequired[str | None]


class PlanReviewCommand(TypedDict):
    """Payload accepted when resuming a plan review interrupt."""

    action: PlanReviewAction
    todos: NotRequired[list[dict[str, str]]]
    plan_meta: NotRequired[PlanMeta]
    feedback: NotRequired[str]
    reason: NotRequired[str]


class RuntimeState(PlanningState):
    """Runtime state extending the `TodoListMiddleware` planning state."""

    mode: RuntimeMode
    status: NotRequired[str]
    plan_meta: NotRequired[PlanMeta | None]
    plan_review_count: NotRequired[int]
    plan_feedback: NotRequired[str | None]
    fast_tool_round_used: NotRequired[bool]
    fsagent_todo_index: NotRequired[int]
    fsagent_todo_content: NotRequired[str]
    execution_log: NotRequired[list[ExecutionLogEntry]]
    final_response: NotRequired[str | None]
    artifacts: NotRequired[list[ArtifactRecord]]
    tool_calls: NotRequired[list[ToolCallRecord]]
    evidence: NotRequired[list[EvidenceRecord]]
    verification: NotRequired[list[VerificationRecord]]
