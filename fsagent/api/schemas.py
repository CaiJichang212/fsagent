"""Pydantic schemas for the fsagent HTTP API."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

RuntimeMode = Literal["fast", "plan"]
RiskLevel = Literal["low", "medium", "high", "critical"]
ReviewKind = Literal["plan_review", "tool_review", "deviation_review", "mcp_review"]
ReviewStatus = Literal["pending", "approved", "modified", "denied", "cancelled", "expired"]
ReviewAction = Literal["approve", "edit", "retry", "cancel", "modify", "deny", "replan", "respond"]
SessionStatus = Literal[
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
]
TodoStatus = Literal["pending", "in_progress", "completed", "failed", "blocked"]
ExecutionStatus = Literal["pending", "in_progress", "completed", "skipped", "failed", "blocked"]
VerificationStatus = Literal["passed", "failed", "skipped", "manual"]


class ApiModel(BaseModel):
    """Base schema that accepts Python names and emits frontend aliases."""

    model_config = ConfigDict(populate_by_name=True)


class TodoItem(ApiModel):
    """Todo item displayed by the frontend."""

    id: str | None = None
    content: str
    status: TodoStatus
    risk: RiskLevel = "low"
    depends_on: list[str] = Field(default_factory=list, alias="dependsOn")
    evidence_ids: list[str] = Field(default_factory=list, alias="evidenceIds")
    verification_ids: list[str] = Field(default_factory=list, alias="verificationIds")
    failure_reason: str | None = Field(None, alias="failureReason")


class PlanMeta(ApiModel):
    """Planner metadata displayed during review."""

    goal: str
    assumptions: list[str] = Field(default_factory=list)
    final_output_format: str | None = None
    verification: list[str] = Field(default_factory=list)


class ExecutionLogEntry(ApiModel):
    """Execution result for one todo."""

    id: str | None = None
    todo_id: str | None = Field(None, alias="todoId")
    content: str
    status: ExecutionStatus
    result: str | None = None
    error: str | None = None
    started_at: str | None = Field(None, alias="startedAt")
    completed_at: str | None = Field(None, alias="completedAt")
    tool_call_ids: list[str] = Field(default_factory=list, alias="toolCallIds")
    artifact_ids: list[str] = Field(default_factory=list, alias="artifactIds")
    verification_ids: list[str] = Field(default_factory=list, alias="verificationIds")


class TimelineEvent(ApiModel):
    """Frontend timeline event."""

    id: str
    kind: str
    message: str
    at: str
    phase: str | None = None
    severity: Literal["info", "warning", "error"] = "info"
    correlation_id: str | None = Field(None, alias="correlationId")
    fields: dict[str, object] = Field(default_factory=dict)


class ToolCallRecord(ApiModel):
    """Tool lifecycle and policy data."""

    id: str
    todo_id: str | None = Field(None, alias="todoId")
    name: str
    risk: RiskLevel = "low"
    status: str
    input_summary: str | None = Field(None, alias="inputSummary")
    output_summary: str | None = Field(None, alias="outputSummary")
    review_id: str | None = Field(None, alias="reviewId")
    duration_ms: float | None = Field(None, alias="durationMs")


class ArtifactRecord(ApiModel):
    """Artifact reference captured by the runtime."""

    id: str
    kind: str
    path: str | None = None
    summary: str
    size: int | None = None
    sha256: str | None = None
    redaction_status: str = Field("none", alias="redactionStatus")
    created_at: str | None = Field(None, alias="createdAt")


class EvidenceRecord(ApiModel):
    """Evidence linked to a todo, tool call, artifact, or verifier."""

    id: str
    todo_id: str | None = Field(None, alias="todoId")
    tool_call_id: str | None = Field(None, alias="toolCallId")
    artifact_id: str | None = Field(None, alias="artifactId")
    summary: str
    source: Literal["tool", "runtime", "verifier", "user"] = "runtime"
    created_at: str | None = Field(None, alias="createdAt")


class VerificationRecord(ApiModel):
    """Verification outcome exposed in session snapshots."""

    id: str
    todo_id: str | None = Field(None, alias="todoId")
    command: str | None = None
    status: VerificationStatus
    exit_code: int | None = Field(None, alias="exitCode")
    stdout_summary: str | None = Field(None, alias="stdoutSummary")
    stderr_summary: str | None = Field(None, alias="stderrSummary")
    artifact_id: str | None = Field(None, alias="artifactId")
    reason: str | None = None
    created_at: str | None = Field(None, alias="createdAt")


class ReviewRecord(ApiModel):
    """Generic review gate for plan, tool, deviation, and MCP approvals."""

    id: str
    kind: ReviewKind
    status: ReviewStatus = "pending"
    risk: RiskLevel = "low"
    subject: dict[str, object] = Field(default_factory=dict)
    proposed_input_summary: str | None = Field(None, alias="proposedInputSummary")
    allowed_actions: list[str] = Field(default_factory=list, alias="allowedActions")
    decision: dict[str, object] | None = None
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")
    expires_at: str | None = Field(None, alias="expiresAt")


class RunRequest(ApiModel):
    """Create-run request from the task composer."""

    mode: RuntimeMode
    message: str
    model: str | None = None
    thinking: bool = False
    mcp_enabled: bool = Field(default=False, alias="mcpEnabled")
    trust_project_mcp: bool = Field(default=False, alias="trustProjectMcp")
    mcp_config_path: str | None = Field(None, alias="mcpConfigPath")
    profile: str | None = None
    backend_profile: str | None = Field(None, alias="backendProfile")
    permission_profile: str | None = Field(None, alias="permissionProfile")
    tool_policy_profile: str | None = Field(None, alias="toolPolicyProfile")


class ReviewRequest(ApiModel):
    """Plan review action request."""

    action: Literal["approve", "edit", "retry", "cancel"]
    review_id: str | None = Field(None, alias="reviewId")
    todos: list[TodoItem] | None = None
    plan_meta: PlanMeta | None = Field(None, alias="planMeta")
    feedback: str | None = None
    reason: str | None = None


class ReviewDecisionRequest(ApiModel):
    """Generic review decision request."""

    action: ReviewAction
    review_id: str | None = Field(None, alias="reviewId")
    edited_subject: dict[str, object] | None = Field(None, alias="editedSubject")
    feedback: str | None = None
    reason: str | None = None


class SessionResponse(ApiModel):
    """Full frontend session snapshot."""

    session_id: str = Field(alias="sessionId")
    thread_id: str = Field(alias="threadId")
    mode: RuntimeMode
    status: SessionStatus
    message: str
    model: str
    thinking: bool
    mcp_enabled: bool = Field(alias="mcpEnabled")
    trust_project_mcp: bool = Field(alias="trustProjectMcp")
    todos: list[TodoItem] = Field(default_factory=list)
    plan_meta: PlanMeta | None = Field(None, alias="planMeta")
    execution_log: list[ExecutionLogEntry] = Field(default_factory=list, alias="executionLog")
    timeline: list[TimelineEvent] = Field(default_factory=list)
    pending_review: ReviewRecord | None = Field(None, alias="pendingReview")
    reviews: list[ReviewRecord] = Field(default_factory=list)
    tool_calls: list[ToolCallRecord] = Field(default_factory=list, alias="toolCalls")
    artifacts: list[ArtifactRecord] = Field(default_factory=list)
    evidence: list[EvidenceRecord] = Field(default_factory=list)
    verification: list[VerificationRecord] = Field(default_factory=list)
    final_response: str | None = Field(None, alias="finalResponse")
    error: str | None = None
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class HealthResponse(ApiModel):
    """Health check response."""

    status: Literal["ok"]


class ModelConfigItem(ApiModel):
    """One configured model option exposed to the frontend."""

    name: str
    display_name: str = Field(alias="displayName")
    supports_thinking: bool = Field(alias="supportsThinking")
    default_thinking_enabled: bool = Field(alias="defaultThinkingEnabled")
    thinking_switch_type: Literal["toggle", "fixed", "none"] = Field(alias="thinkingSwitchType")
    sampling: dict[str, dict[str, float | int]] = Field(default_factory=dict)


class ModelConfigResponse(ApiModel):
    """Frontend model selector configuration."""

    models: list[ModelConfigItem]
