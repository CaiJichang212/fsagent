"""Pydantic schemas for the fsagent HTTP API."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

RuntimeMode = Literal["fast", "plan"]
SessionStatus = Literal[
    "idle",
    "running",
    "planning",
    "awaiting_plan_review",
    "editing_plan",
    "retrying_plan",
    "executing",
    "awaiting_tool_review",
    "completed",
    "cancelled",
    "failed",
]
TodoStatus = Literal["pending", "in_progress", "completed", "failed"]
ExecutionStatus = Literal["pending", "in_progress", "completed", "skipped", "failed"]


class ApiModel(BaseModel):
    """Base schema that accepts Python names and emits frontend aliases."""

    model_config = ConfigDict(populate_by_name=True)


class TodoItem(ApiModel):
    """Todo item displayed by the frontend."""

    content: str
    status: TodoStatus


class PlanMeta(ApiModel):
    """Planner metadata displayed during review."""

    goal: str
    assumptions: list[str] = Field(default_factory=list)
    final_output_format: str | None = None


class ExecutionLogEntry(ApiModel):
    """Execution result for one todo."""

    content: str
    status: ExecutionStatus
    result: str | None = None
    error: str | None = None


class TimelineEvent(ApiModel):
    """Frontend timeline event."""

    id: str
    kind: str
    message: str
    at: str


class RunRequest(ApiModel):
    """Create-run request from the task composer."""

    mode: RuntimeMode
    message: str
    model: str | None = None
    thinking: bool = False
    mcp_enabled: bool = Field(default=False, alias="mcpEnabled")
    trust_project_mcp: bool = Field(default=False, alias="trustProjectMcp")
    mcp_config_path: str | None = Field(None, alias="mcpConfigPath")


class ReviewRequest(ApiModel):
    """Plan review action request."""

    action: Literal["approve", "edit", "retry", "cancel"]
    todos: list[TodoItem] | None = None
    plan_meta: PlanMeta | None = Field(None, alias="planMeta")
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
