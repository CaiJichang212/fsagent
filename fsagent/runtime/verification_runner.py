"""Allowlisted verification command execution."""

from __future__ import annotations

import asyncio
import inspect
import shlex
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from subprocess import CompletedProcess
from typing import TYPE_CHECKING, Literal, TypeAlias

if TYPE_CHECKING:
    from fsagent.runtime.state import VerificationRecord

_ALLOWLISTED_PREFIXES = (
    ("uv", "run", "--group", "test", "pytest"),
    ("uv", "run", "--group", "test", "ruff", "check"),
    ("uv", "run", "--group", "test", "ruff", "format"),
    ("npm", "run", "build"),
)
_SUMMARY_LIMIT = 2000
_NOT_ALLOWLISTED_REASON = "Verification command is not allowlisted."
_TIMED_OUT_REASON = "Verification command timed out."

ExecutorResult: TypeAlias = CompletedProcess[str] | Awaitable[CompletedProcess[str]]
VerificationExecutor: TypeAlias = Callable[["VerificationCommand"], ExecutorResult]


@dataclass(frozen=True, slots=True)
class VerificationCommand:
    """A command permitted for verification execution."""

    command: str
    cwd: Path | None
    timeout_seconds: int = 120


async def run_verification_commands(
    commands: Sequence[str | VerificationCommand],
    *,
    cwd: str | Path | None = None,
    executor: VerificationExecutor | None = None,
) -> list[VerificationRecord]:
    """Run allowlisted verification commands and return normalized records."""
    base_cwd = Path(cwd) if cwd is not None else None
    records: list[VerificationRecord] = []
    for index, command in enumerate(commands, start=1):
        verification_command = _coerce_command(command, cwd=base_cwd)
        if not _is_allowlisted(verification_command.command):
            records.append(_record(index, verification_command, status="skipped", reason=_NOT_ALLOWLISTED_REASON))
            continue
        completed = await _execute(verification_command, executor=executor)
        status = "passed" if completed.returncode == 0 else "failed"
        records.append(
            _record(
                index,
                verification_command,
                status=status,
                exit_code=completed.returncode,
                stdout_summary=_summary(completed.stdout),
                stderr_summary=_summary(completed.stderr),
            )
        )
    return records


def _coerce_command(command: str | VerificationCommand, *, cwd: Path | None) -> VerificationCommand:
    if isinstance(command, VerificationCommand):
        return command
    return VerificationCommand(command=command, cwd=cwd)


def _is_allowlisted(command: str) -> bool:
    try:
        parts = tuple(shlex.split(command))
    except ValueError:
        return False
    return any(parts[: len(prefix)] == prefix for prefix in _ALLOWLISTED_PREFIXES)


async def _execute(
    command: VerificationCommand,
    *,
    executor: VerificationExecutor | None,
) -> CompletedProcess[str]:
    if executor is not None:
        try:
            result = executor(command)
            if inspect.isawaitable(result):
                completed = await result
            else:
                completed = result
        except TimeoutError:
            return CompletedProcess(
                args=command.command,
                returncode=124,
                stdout="",
                stderr=_TIMED_OUT_REASON,
            )
        return completed
    return await _run_subprocess(command)


async def _run_subprocess(command: VerificationCommand) -> CompletedProcess[str]:
    parts = shlex.split(command.command)
    process = await asyncio.create_subprocess_exec(
        *parts,
        cwd=str(command.cwd) if command.cwd is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=command.timeout_seconds)
    except TimeoutError:
        process.kill()
        await process.communicate()
        return CompletedProcess(args=command.command, returncode=124, stdout="", stderr=_TIMED_OUT_REASON)
    return CompletedProcess(
        args=command.command,
        returncode=process.returncode or 0,
        stdout=stdout.decode(errors="replace"),
        stderr=stderr.decode(errors="replace"),
    )


def _record(
    index: int,
    command: VerificationCommand,
    *,
    status: Literal["passed", "failed", "skipped", "manual"],
    exit_code: int | None = None,
    stdout_summary: str | None = None,
    stderr_summary: str | None = None,
    reason: str | None = None,
) -> VerificationRecord:
    return {
        "id": f"verification-{index:03d}",
        "todo_id": None,
        "command": command.command,
        "status": status,
        "exit_code": exit_code,
        "stdout_summary": stdout_summary,
        "stderr_summary": stderr_summary,
        "artifact_id": None,
        "reason": reason,
        "created_at": None,
    }


def _summary(value: str | None) -> str | None:
    if not value:
        return None
    return value[:_SUMMARY_LIMIT]
