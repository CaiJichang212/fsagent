"""Plan generation helpers."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig

from fsagent.runtime.approval import normalize_plan, normalize_plan_meta
from fsagent.runtime.state import PlanMeta, TodoItem


@dataclass(frozen=True, slots=True)
class GeneratedPlan:
    """Planner output."""

    todos: list[TodoItem]
    plan_meta: PlanMeta


async def generate_plan(
    *,
    agent: object,
    content: str,
    feedback: str | None = None,
    config: RunnableConfig | None = None,
) -> GeneratedPlan:
    """Generate todos by invoking a planner agent.

    Args:
        agent: Deep agent containing `TodoListMiddleware`.
        content: User task.
        feedback: Optional retry feedback.
        config: Optional LangGraph runnable config.

    Returns:
        Generated todos and metadata.
    """
    prompt = _planner_prompt(content=content, feedback=feedback)
    result = await agent.ainvoke({"messages": [HumanMessage(content=prompt)], "mode": "plan"}, config=config)  # type: ignore[attr-defined]
    todos = _pending_review_todos(_extract_todos(result))
    return GeneratedPlan(todos=todos, plan_meta=_extract_plan_meta(result, default_goal=content))


def _planner_prompt(*, content: str, feedback: str | None) -> str:
    if feedback:
        return f"Task:\n{content}\n\nRegenerate the plan using this feedback:\n{feedback}"
    return f"Task:\n{content}"


def _extract_todos(result: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
    raw_todos = result.get("todos")
    if isinstance(raw_todos, list):
        return raw_todos
    msg = "Planner did not produce todos."
    raise ValueError(msg)


def _pending_review_todos(raw_todos: Sequence[Mapping[str, Any]]) -> list[TodoItem]:
    todos = normalize_plan(raw_todos)
    return [{"content": todo["content"], "status": "pending"} for todo in todos]


def _extract_plan_meta(result: Mapping[str, Any], *, default_goal: str) -> PlanMeta:
    raw_meta = result.get("plan_meta")
    if isinstance(raw_meta, Mapping):
        return normalize_plan_meta(raw_meta, default_goal=default_goal)
    return normalize_plan_meta(None, default_goal=default_goal)
