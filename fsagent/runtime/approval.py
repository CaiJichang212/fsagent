"""Plan review helpers."""

from collections.abc import Mapping, Sequence
from typing import Any, cast

from fsagent.runtime.state import PlanMeta, PlanReviewAction, PlanReviewCommand, TodoItem, TodoStatus

_TODO_STATUSES = {"pending", "in_progress", "completed"}
_ACTIONS = {"approve", "edit", "retry", "cancel"}


def normalize_plan(todos: Sequence[Mapping[str, Any]]) -> list[TodoItem]:
    """Normalize user-edited todos into the `TodoListMiddleware` shape.

    Args:
        todos: Raw todo dictionaries from a planner or edit command.

    Returns:
        Normalized todos containing only `content` and `status`.

    Raises:
        ValueError: If no todos are supplied or any todo is invalid.
    """
    if not todos:
        msg = "Plan must include at least one todo."
        raise ValueError(msg)

    normalized: list[TodoItem] = []
    for index, todo in enumerate(todos, start=1):
        content = str(todo.get("content", "")).strip()
        if not content:
            msg = f"Todo {index} must include non-empty content."
            raise ValueError(msg)
        status = str(todo.get("status", "pending"))
        if status not in _TODO_STATUSES:
            msg = f"Invalid todo status: {status}"
            raise ValueError(msg)
        normalized.append({"content": content, "status": cast("TodoStatus", status)})
    return normalized


def normalize_plan_meta(plan_meta: Mapping[str, Any] | None, *, default_goal: str = "") -> PlanMeta:
    """Normalize plan metadata for review display.

    Args:
        plan_meta: Raw plan metadata.
        default_goal: Goal used when metadata omits `goal`.

    Returns:
        Normalized plan metadata.
    """
    if plan_meta is None:
        return {"goal": default_goal}
    goal = str(plan_meta.get("goal") or default_goal).strip()
    normalized: PlanMeta = {"goal": goal}
    assumptions = plan_meta.get("assumptions")
    if isinstance(assumptions, list):
        normalized["assumptions"] = [str(item) for item in assumptions if str(item).strip()]
    output_format = plan_meta.get("final_output_format")
    if output_format is not None:
        normalized["final_output_format"] = str(output_format)
    verification = plan_meta.get("verification")
    if isinstance(verification, list):
        normalized["verification"] = [str(item).strip() for item in verification if str(item).strip()]
    return normalized


def normalize_review_command(payload: Mapping[str, Any]) -> PlanReviewCommand:
    """Normalize a plan review resume payload.

    Args:
        payload: Raw resume payload.

    Returns:
        Normalized review command.

    Raises:
        ValueError: If the action or required fields are invalid.
    """
    action = str(payload.get("action", ""))
    if action not in _ACTIONS:
        msg = f"Invalid plan review action: {action}"
        raise ValueError(msg)

    command: PlanReviewCommand = {"action": cast("PlanReviewAction", action)}
    if action == "edit":
        raw_todos = payload.get("todos")
        if not isinstance(raw_todos, list):
            msg = "edit requires todos."
            raise ValueError(msg)
        command["todos"] = normalize_plan(raw_todos)
        raw_meta = payload.get("plan_meta")
        if isinstance(raw_meta, Mapping):
            command["plan_meta"] = normalize_plan_meta(raw_meta)
    if action == "retry" and payload.get("feedback"):
        command["feedback"] = str(payload["feedback"])
    if action == "cancel" and payload.get("reason"):
        command["reason"] = str(payload["reason"])
    return command


def build_plan_review_payload(
    *,
    todos: Sequence[Mapping[str, Any]],
    plan_meta: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Build the interrupt payload for plan review.

    Args:
        todos: Current plan todos.
        plan_meta: Plan metadata.

    Returns:
        Serializable interrupt payload.
    """
    normalized = normalize_plan(todos)
    return {
        "kind": "plan_review",
        "todos": normalized,
        "plan_meta": normalize_plan_meta(plan_meta),
        "allowed_actions": ["approve", "edit", "retry", "cancel"],
        "instructions": "Review the plan above. Approve to execute all items.",
    }
