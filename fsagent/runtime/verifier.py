"""Verification records and completion status for plan execution."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

from fsagent.runtime.verification_runner import VerificationExecutor, run_verification_commands

if TYPE_CHECKING:
    from fsagent.runtime.state import ExecutionLogEntry, PlanMeta, TodoItem, VerificationRecord

VerificationCompletionStatus = Literal["completed", "needs_revision"]
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


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
    return _verification_result(todos=todos, execution_log=execution_log, records=records)


async def verify_execution_async(
    *,
    todos: Sequence[TodoItem],
    execution_log: Sequence[ExecutionLogEntry],
    plan_meta: PlanMeta | Mapping[str, object] | None = None,
    verification: Sequence[Mapping[str, object]] | None = None,
    cwd: str | Path | None = None,
    executor: VerificationExecutor | None = None,
) -> VerificationResult:
    """Normalize or run verification records and determine completion status."""
    records = _verification_records(verification)
    if not records:
        commands = _verification_commands_from_plan_meta(plan_meta)
        if commands:
            records = await run_verification_commands(commands, cwd=cwd or _PROJECT_ROOT, executor=executor)
    return _verification_result(todos=todos, execution_log=execution_log, records=records)


def _verification_result(
    *,
    todos: Sequence[TodoItem],
    execution_log: Sequence[ExecutionLogEntry],
    records: list[VerificationRecord],
) -> VerificationResult:
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


def _verification_commands_from_plan_meta(plan_meta: PlanMeta | Mapping[str, object] | None) -> list[str]:
    if not isinstance(plan_meta, Mapping):
        return []
    verification = plan_meta.get("verification")
    if not isinstance(verification, list):
        return []
    return [str(item).strip() for item in verification if str(item).strip()]


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
