"""Verification records and completion status for plan execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, cast

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from fsagent.runtime.state import ExecutionLogEntry, TodoItem, VerificationRecord

VerificationCompletionStatus = Literal["completed", "needs_revision"]


@dataclass(frozen=True, slots=True)
class VerificationResult:
    """Verification records plus the session status they imply."""

    status: VerificationCompletionStatus
    verification: list[VerificationRecord]


def verify_execution(
    *,
    todos: Sequence[TodoItem],
    execution_log: Sequence[ExecutionLogEntry],
    verification: Sequence[Mapping[str, object]] | None = None,
) -> VerificationResult:
    """Normalize verification records and determine completion status."""
    records = _verification_records(verification)
    if not records:
        records = [
            {
                "id": "verification-001",
                "todo_id": None,
                "command": None,
                "status": "skipped",
                "exit_code": None,
                "stdout_summary": None,
                "stderr_summary": None,
                "artifact_id": None,
                "reason": "No verification command was provided.",
                "created_at": None,
            }
        ]
    status: VerificationCompletionStatus = (
        "needs_revision"
        if any(item["status"] == "failed" for item in records) or _has_execution_failure(todos, execution_log)
        else "completed"
    )
    return VerificationResult(status=status, verification=records)


def _has_execution_failure(
    todos: Sequence[TodoItem],
    execution_log: Sequence[ExecutionLogEntry],
) -> bool:
    return any(item.get("status") in {"failed", "blocked"} for item in todos) or any(
        item.get("status") in {"failed", "blocked"} for item in execution_log
    )


def _verification_records(verification: Sequence[Mapping[str, object]] | None) -> list[VerificationRecord]:
    if verification is None:
        return []
    records: list[VerificationRecord] = []
    for index, item in enumerate(verification, start=1):
        status = _verification_status(item.get("status"))
        records.append(
            {
                "id": str(item.get("id") or f"verification-{index:03d}"),
                "todo_id": _optional_str(item.get("todo_id") or item.get("todoId")),
                "command": _optional_str(item.get("command")),
                "status": status,
                "exit_code": _optional_int(item.get("exit_code") or item.get("exitCode")),
                "stdout_summary": _optional_str(item.get("stdout_summary") or item.get("stdoutSummary")),
                "stderr_summary": _optional_str(item.get("stderr_summary") or item.get("stderrSummary")),
                "artifact_id": _optional_str(item.get("artifact_id") or item.get("artifactId")),
                "reason": _optional_str(item.get("reason")),
                "created_at": _optional_str(item.get("created_at") or item.get("createdAt")),
            }
        )
    return records


def _optional_str(value: object) -> str | None:
    return str(value) if value is not None else None


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) else None


def _verification_status(value: object) -> Literal["passed", "failed", "skipped", "manual"]:
    status = str(value or "skipped")
    if status in {"passed", "failed", "skipped", "manual"}:
        return cast("Literal['passed', 'failed', 'skipped', 'manual']", status)
    return "skipped"
