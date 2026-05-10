"""Typed state for the fast/plan runtime."""

from typing import Literal, NotRequired, TypedDict

from langchain.agents.middleware.todo import PlanningState

RuntimeMode = Literal["fast", "plan"]
TodoStatus = Literal["pending", "in_progress", "completed"]
ExecutionStatus = Literal["pending", "in_progress", "completed", "skipped", "blocked", "failed"]
PlanReviewAction = Literal["approve", "edit", "retry", "cancel"]


class TodoItem(TypedDict):
    """Todo item compatible with `TodoListMiddleware`."""

    content: str
    status: TodoStatus


class PlanMeta(TypedDict):
    """Plan metadata shown during plan review."""

    goal: str
    assumptions: NotRequired[list[str]]
    final_output_format: NotRequired[str]


class ExecutionLogEntry(TypedDict):
    """Detailed execution result for one todo item."""

    content: str
    status: ExecutionStatus
    result: NotRequired[str | None]
    error: NotRequired[str | None]


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
    plan_meta: NotRequired[PlanMeta | None]
    plan_review_count: NotRequired[int]
    plan_feedback: NotRequired[str | None]
    fast_tool_round_used: NotRequired[bool]
    fsagent_todo_index: NotRequired[int]
    fsagent_todo_content: NotRequired[str]
    execution_log: NotRequired[list[ExecutionLogEntry]]
    final_response: NotRequired[str | None]
    artifacts: NotRequired[list[str]]
