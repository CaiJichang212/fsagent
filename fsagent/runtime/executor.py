"""Plan executor."""

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig

from fsagent.runtime.state import ExecutionLogEntry, PlanMeta, TodoItem

ProgressEvent = Mapping[str, object]
ProgressCallback = Callable[[ProgressEvent], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """Result of executing an approved plan."""

    todos: list[TodoItem]
    execution_log: list[ExecutionLogEntry]
    final_result: str


async def execute_plan(
    *,
    agent: object,
    todos: Sequence[TodoItem],
    plan_meta: PlanMeta | None = None,
    config: RunnableConfig | None = None,
    on_event: ProgressCallback | None = None,
) -> ExecutionResult:
    """Execute pending todos in order, skipping completed items.

    Args:
        agent: Deep agent used to execute each item.
        todos: Approved todos.
        plan_meta: Plan metadata.
        config: Optional LangGraph runnable config.
        on_event: Optional callback for incremental execution progress.

    Returns:
        Updated todos plus detailed execution log.
    """
    updated: list[TodoItem] = [dict(todo) for todo in todos]
    execution_log: list[ExecutionLogEntry] = []
    results: list[str] = []

    for index, todo in enumerate(updated):
        if todo["status"] == "completed":
            entry: ExecutionLogEntry = {
                "content": todo["content"],
                "status": "skipped",
                "result": "Already completed.",
            }
            execution_log.append(entry)
            continue

        todo["status"] = "in_progress"
        current_todo: TodoItem = {"content": todo["content"], "status": "in_progress"}
        await _emit(
            on_event,
            {
                "kind": "todo.started",
                "message": f"开始执行 {todo['content']}",
                "todos": _todo_snapshot(updated),
                "execution_log": _log_snapshot(execution_log),
            },
        )
        try:
            prompt = _execution_prompt(todo=current_todo, index=index, total=len(updated), plan_meta=plan_meta)
            result = await agent.ainvoke(  # type: ignore[attr-defined]
                {
                    "messages": [HumanMessage(content=prompt)],
                    "todos": [dict(current_todo)],
                    "fsagent_todo_index": index + 1,
                    "fsagent_todo_content": todo["content"],
                },
                config=_todo_execution_config(config, index),
            )
        except Exception as exc:  # noqa: BLE001  # per-item execution errors are logged and do not stop later items
            todo["status"] = "pending"
            msg = str(exc)
            execution_log.append({"content": todo["content"], "status": "failed", "error": msg})
            results.append(f"{todo['content']}: failed - {msg}")
            await _emit(
                on_event,
                {
                    "kind": "todo.failed",
                    "message": f"执行失败 {todo['content']}: {msg}",
                    "todos": _todo_snapshot(updated),
                    "execution_log": _log_snapshot(execution_log),
                },
            )
            continue

        item_result = _extract_result_text(result)
        todo["status"] = "completed"
        updated[index] = todo
        execution_log.append({"content": todo["content"], "status": "completed", "result": item_result})
        results.append(item_result)
        await _emit(
            on_event,
            {
                "kind": "todo.completed",
                "message": f"完成 {todo['content']}",
                "todos": _todo_snapshot(updated),
                "execution_log": _log_snapshot(execution_log),
            },
        )

    final_result = "\n".join(result for result in results if result) or "Plan execution completed."
    return ExecutionResult(todos=updated, execution_log=execution_log, final_result=final_result)


def _execution_prompt(*, todo: TodoItem, index: int, total: int, plan_meta: PlanMeta | None) -> str:
    goal = (plan_meta or {}).get("goal", "")
    return (
        "You are executing exactly one approved plan item.\n"
        "Do not execute, test, summarize, or update any other plan item in this run.\n"
        "If setup is needed for this item, do only the minimum setup and report it as setup.\n"
        "Return only the result and evidence for the current item.\n\n"
        f"Goal: {goal}\n\n"
        f"Item: {index + 1} of {total}\n\n"
        f"Current todo: {todo['content']}\n\n"
        "The todo state provided to you contains only this current item. "
        "Use write_todos only to keep this single item synchronized."
    )


def _todo_execution_config(config: RunnableConfig | None, index: int) -> RunnableConfig | None:
    if config is None:
        return None
    copied: dict[str, Any] = {**config}
    configurable = dict(copied.get("configurable") or {})
    thread_id = configurable.get("thread_id")
    if thread_id:
        configurable["thread_id"] = f"{thread_id}:todo:{index + 1}"
    copied["configurable"] = configurable
    return copied


def _extract_result_text(result: Mapping[str, Any]) -> str:
    final_response = result.get("final_response")
    if isinstance(final_response, str) and final_response:
        return final_response
    messages = result.get("messages", [])
    for message in reversed(messages):
        content = getattr(message, "content", None)
        if isinstance(content, str) and content:
            return content
    return "Completed."


async def _emit(on_event: ProgressCallback | None, event: ProgressEvent) -> None:
    if on_event is not None:
        await on_event(event)


def _todo_snapshot(todos: Sequence[TodoItem]) -> list[TodoItem]:
    return [dict(todo) for todo in todos]


def _log_snapshot(entries: Sequence[ExecutionLogEntry]) -> list[ExecutionLogEntry]:
    return [dict(entry) for entry in entries]
