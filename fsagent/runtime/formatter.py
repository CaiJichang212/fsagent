"""Final result formatter for plan mode."""

from collections.abc import Mapping, Sequence
from typing import Any

from fsagent.runtime.state import ArtifactRecord, EvidenceRecord, ExecutionLogEntry, TodoItem, VerificationRecord


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
    result: str,
    todos: Sequence[TodoItem],
    execution_log: Sequence[ExecutionLogEntry],
    artifacts: Sequence[str | ArtifactRecord] | None = None,
    evidence: Sequence[EvidenceRecord] | None = None,
    verification: Sequence[VerificationRecord] | None = None,
) -> str:
    """Format a runtime result using the required report template.

    Args:
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
            result,
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
