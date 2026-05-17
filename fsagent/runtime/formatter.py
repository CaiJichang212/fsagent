"""Final result formatter for plan mode."""

from collections.abc import Mapping, Sequence
from typing import Any

from fsagent.runtime.state import ArtifactRecord, EvidenceRecord, ExecutionLogEntry, TodoItem, VerificationRecord

_SUMMARY_MAX_CHARS = 160


def _result_lines(
    *,
    result: str,
    status: str,
    execution_log: Sequence[ExecutionLogEntry],
) -> list[str]:
    if status == "needs_revision":
        completed = sum(1 for item in execution_log if item.get("status") == "completed")
        skipped = sum(1 for item in execution_log if item.get("status") == "skipped")
        blocked = sum(1 for item in execution_log if item.get("status") == "blocked")
        failed = sum(1 for item in execution_log if item.get("status") == "failed")
        return [
            "Execution requires revision.",
            (
                f"Completed items: {completed}; skipped items: {skipped}; "
                f"blocked items: {blocked}; failed items: {failed}."
            ),
            "Agent-authored completion summary was omitted because some plan items failed or were blocked.",
        ]
    return [result]


def _is_structured_markdown(text: str) -> bool:
    stripped = text.strip()
    if "\n" in stripped:
        return True
    return stripped.startswith(("#", "-", "*", "|", "1.")) or "```" in stripped


def _single_line_summary(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    summary = " ".join(value.split())
    if not summary:
        return None
    if len(summary) > _SUMMARY_MAX_CHARS:
        return f"{summary[: _SUMMARY_MAX_CHARS - 3]}..."
    return summary


def _completed_summary(entry: ExecutionLogEntry) -> str:
    result = entry.get("result")
    if isinstance(result, str) and result.strip():
        if _is_structured_markdown(result):
            return f"{entry['content']} - Detailed output captured in Result section."
        summary = _single_line_summary(result)
        if summary is not None:
            return summary
    return entry["content"]


def _summary_lines(execution_log: Sequence[ExecutionLogEntry]) -> list[str]:
    executed: list[str] = []
    skipped: list[str] = []
    blocked: list[str] = []
    failed: list[str] = []
    for entry in execution_log:
        if entry["status"] == "completed":
            executed.append(f"- {_completed_summary(entry)}")
        elif entry["status"] == "skipped":
            detail = entry.get("result") or "Already completed."
            skipped.append(f"- {entry['content']} - {detail}")
        elif entry["status"] == "blocked":
            detail = entry.get("error") or entry.get("result") or entry["status"]
            blocked.append(f"- {entry['content']} - {detail}")
        elif entry["status"] == "failed":
            detail = entry.get("error") or entry.get("result") or entry["status"]
            failed.append(f"- {entry['content']} - {detail}")

    lines: list[str] = ["### Executed"]
    lines.extend(executed or ["- No executed items recorded."])
    if skipped:
        lines.extend(["", "### Skipped / Already Completed", *skipped])
    if blocked:
        lines.extend(["", "### Blocked", *blocked])
    if failed:
        lines.extend(["", "### Failed", *failed])
    return lines


def _plan_status_line(todo: Mapping[str, Any], log_by_content: Mapping[str, ExecutionLogEntry]) -> str:
    content = str(todo["content"])
    entry = log_by_content.get(content)
    if entry is not None and entry["status"] == "blocked":
        detail = entry.get("error") or entry.get("result") or entry["status"]
        return f"- [!] Blocked: {content} - {detail}"
    if entry is not None and entry["status"] == "failed":
        detail = entry.get("error") or entry.get("result") or entry["status"]
        return f"- [!] Failed: {content} - {detail}"
    if entry is not None and entry["status"] == "skipped":
        detail = entry.get("result") or "Already completed."
        return f"- [-] Skipped / already completed: {content} - {detail}"
    if todo.get("status") == "completed":
        return f"- [x] {content}"
    return f"- [ ] {content}"


def _artifact_lines(artifacts: Sequence[str | ArtifactRecord] | None) -> list[str]:
    lines: list[str] = []
    for artifact in artifacts or []:
        if isinstance(artifact, str):
            lines.append(f"- {artifact}")
        else:
            summary = artifact.get("summary") or artifact.get("path") or artifact["id"]
            lines.append(f"- {artifact['id']}: {summary}")
    return lines or ["- No artifacts recorded."]


def _evidence_lines(evidence: Sequence[EvidenceRecord] | None) -> list[str]:
    return [f"- {item['id']}: {item['summary']}" for item in evidence or []] or ["- No evidence records."]


def _verification_lines(verification: Sequence[VerificationRecord] | None) -> list[str]:
    lines: list[str] = []
    for item in verification or []:
        detail = item.get("command") or item.get("reason") or "manual verification"
        lines.append(f"- {item['id']}: {item['status']} - {detail}")
    return lines or ["- No verification records."]


def format_final_report(
    *,
    status: str = "completed",
    result: str,
    todos: Sequence[TodoItem],
    execution_log: Sequence[ExecutionLogEntry],
    artifacts: Sequence[str | ArtifactRecord] | None = None,
    evidence: Sequence[EvidenceRecord] | None = None,
    verification: Sequence[VerificationRecord] | None = None,
) -> str:
    """Format a runtime result using the required report template.

    Args:
        status: Overall runtime status after verification.
        result: Final result text.
        todos: Current todo status.
        execution_log: Detailed execution records.
        artifacts: Optional artifact records or legacy evidence lines.
        evidence: Optional evidence records.
        verification: Optional verification records.

    Returns:
        Markdown final report.
    """
    log_by_content = {entry["content"]: entry for entry in execution_log}
    return "\n".join(
        [
            "## Result",
            *_result_lines(result=result, status=status, execution_log=execution_log),
            "",
            "## Execution Summary",
            *_summary_lines(execution_log),
            "",
            "## Plan Status",
            *[_plan_status_line(todo, log_by_content) for todo in todos],
            "",
            "## Artifacts And Evidence",
            *_artifact_lines(artifacts),
            "",
            "## Evidence",
            *_evidence_lines(evidence),
            "",
            "## Verification",
            *_verification_lines(verification),
        ]
    )
