"""Plan executor."""

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig

from fsagent.runtime.state import EvidenceRecord, ExecutionLogEntry, PlanMeta, TodoItem

ProgressEvent = Mapping[str, object]
ProgressCallback = Callable[[ProgressEvent], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """Result of executing an approved plan."""

    todos: list[TodoItem]
    execution_log: list[ExecutionLogEntry]
    evidence: list[EvidenceRecord]
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
    updated: list[TodoItem] = [_normalize_todo(todo, index) for index, todo in enumerate(todos, start=1)]
    execution_log: list[ExecutionLogEntry] = []
    evidence: list[EvidenceRecord] = []
    results: list[str] = []

    for index, todo in enumerate(updated):
        if todo["status"] == "completed":
            entry: ExecutionLogEntry = {
                "id": f"log-{len(execution_log) + 1:03d}",
                "todo_id": todo["id"],
                "content": todo["content"],
                "status": "skipped",
                "result": "Already completed.",
                "error": None,
                "started_at": None,
                "completed_at": None,
                "tool_call_ids": [],
                "artifact_ids": [],
                "verification_ids": [],
            }
            execution_log.append(entry)
            continue

        todo["status"] = "in_progress"
        current_todo: TodoItem = {**todo, "status": "in_progress"}
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
            todo["status"] = "failed"
            msg = str(exc)
            todo["failure_reason"] = msg
            execution_log.append(
                {
                    "id": f"log-{len(execution_log) + 1:03d}",
                    "todo_id": todo["id"],
                    "content": todo["content"],
                    "status": "failed",
                    "result": None,
                    "error": msg,
                    "started_at": None,
                    "completed_at": None,
                    "tool_call_ids": [],
                    "artifact_ids": [],
                    "verification_ids": [],
                }
            )
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
        evidence_id = f"evidence-{len(evidence) + 1:03d}"
        evidence.append(
            {
                "id": evidence_id,
                "todo_id": todo["id"],
                "tool_call_id": None,
                "artifact_id": None,
                "summary": item_result,
                "source": "runtime",
                "created_at": None,
            }
        )
        todo["status"] = "completed"
        todo["evidence_ids"] = [*todo.get("evidence_ids", []), evidence_id]
        updated[index] = todo
        execution_log.append(
            {
                "id": f"log-{len(execution_log) + 1:03d}",
                "todo_id": todo["id"],
                "content": todo["content"],
                "status": "completed",
                "result": item_result,
                "error": None,
                "started_at": None,
                "completed_at": None,
                "tool_call_ids": [],
                "artifact_ids": [],
                "verification_ids": [],
            }
        )
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
    return ExecutionResult(todos=updated, execution_log=execution_log, evidence=evidence, final_result=final_result)


def _normalize_todo(todo: TodoItem, index: int) -> TodoItem:
    return {
        "id": str(todo.get("id") or f"todo-{index:03d}"),
        "content": todo["content"],
        "status": todo["status"],
        "risk": todo.get("risk", "low"),
        "depends_on": list(todo.get("depends_on", [])),
        "evidence_ids": list(todo.get("evidence_ids", [])),
        "verification_ids": list(todo.get("verification_ids", [])),
        "failure_reason": todo.get("failure_reason"),
    }


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
        "The runtime will update todo status, execution logs, and evidence after this run."
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
