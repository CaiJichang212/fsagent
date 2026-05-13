from pathlib import Path
from subprocess import CompletedProcess

from fsagent.runtime.verification_runner import VerificationCommand, run_verification_commands


async def test_run_verification_commands_executes_allowlisted_command_with_injected_executor(tmp_path: Path):
    calls: list[VerificationCommand] = []

    async def executor(command: VerificationCommand) -> CompletedProcess[str]:
        calls.append(command)
        return CompletedProcess(args=command.command, returncode=0, stdout="tests passed", stderr="")

    records = await run_verification_commands(
        ["uv run --group test pytest fsagent/tests -q"],
        cwd=tmp_path,
        executor=executor,
    )

    assert calls == [VerificationCommand(command="uv run --group test pytest fsagent/tests -q", cwd=tmp_path)]
    assert records == [
        {
            "id": "verification-001",
            "todo_id": None,
            "command": "uv run --group test pytest fsagent/tests -q",
            "status": "passed",
            "exit_code": 0,
            "stdout_summary": "tests passed",
            "stderr_summary": None,
            "artifact_id": None,
            "reason": None,
            "created_at": None,
        }
    ]


async def test_run_verification_commands_skips_non_allowlisted_command(tmp_path: Path):
    calls: list[VerificationCommand] = []

    async def executor(command: VerificationCommand) -> CompletedProcess[str]:
        calls.append(command)
        return CompletedProcess(args=command.command, returncode=0, stdout="should not run", stderr="")

    records = await run_verification_commands(["python -m pytest"], cwd=tmp_path, executor=executor)

    assert calls == []
    assert records == [
        {
            "id": "verification-001",
            "todo_id": None,
            "command": "python -m pytest",
            "status": "skipped",
            "exit_code": None,
            "stdout_summary": None,
            "stderr_summary": None,
            "artifact_id": None,
            "reason": "Verification command is not allowlisted.",
            "created_at": None,
        }
    ]
