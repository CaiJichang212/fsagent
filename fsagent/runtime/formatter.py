"""Final result formatter for plan mode."""

from collections.abc import Mapping, Sequence
from typing import Any

from fsagent.runtime.state import ExecutionLogEntry, TodoItem


def _summary_lines(execution_log: Sequence[ExecutionLogEntry]) -> list[str]:
    executed: list[str] = []
    skipped: list[str] = []
    failed: list[str] = []
    for entry in execution_log:
        result = entry.get("result")
        if entry["status"] == "completed" and result:
            executed.append(f"- {result}")
        elif entry["status"] == "skipped":
            detail = result or "Already completed."
            skipped.append(f"- {entry['content']} - {detail}")
        elif entry["status"] in {"failed", "blocked"}:
            detail = entry.get("error") or result or entry["status"]
            failed.append(f"- {entry['content']} - {detail}")

    lines: list[str] = ["### Executed"]
    lines.extend(executed or ["- No executed items recorded."])
    if skipped:
        lines.extend(["", "### Skipped / Already Completed", *skipped])
    if failed:
        lines.extend(["", "### Failed", *failed])
    return lines


def _plan_status_line(todo: Mapping[str, Any], log_by_content: Mapping[str, ExecutionLogEntry]) -> str:
    content = str(todo["content"])
    entry = log_by_content.get(content)
    if entry is not None and entry["status"] in {"failed", "blocked"}:
        detail = entry.get("error") or entry.get("result") or entry["status"]
        return f"- [!] {content} - {detail}"
    if entry is not None and entry["status"] == "skipped":
        detail = entry.get("result") or "Already completed."
        return f"- [-] Skipped / already completed: {content} - {detail}"
    if todo.get("status") == "completed":
        return f"- [x] {content}"
    return f"- [ ] {content}"


def format_final_report(
    *,
    result: str,
    todos: Sequence[TodoItem],
    execution_log: Sequence[ExecutionLogEntry],
    artifacts: Sequence[str] | None = None,
) -> str:
    """Format a runtime result using the required report template.

    Args:
        result: Final result text.
        todos: Current todo status.
        execution_log: Detailed execution records.
        artifacts: Optional evidence lines.

    Returns:
        Markdown final report.
    """
    log_by_content = {entry["content"]: entry for entry in execution_log}
    artifact_lines = [f"- {artifact}" for artifact in artifacts or []] or ["- No artifacts recorded."]
    return "\n".join(
        [
            "## Result",
            result,
            "",
            "## Execution Summary",
            *_summary_lines(execution_log),
            "",
            "## Plan Status",
            *[_plan_status_line(todo, log_by_content) for todo in todos],
            "",
            "## Artifacts And Evidence",
            *artifact_lines,
        ]
    )
