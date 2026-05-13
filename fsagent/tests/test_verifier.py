from pathlib import Path
from subprocess import CompletedProcess

import fsagent.runtime.verifier as verifier_module
from fsagent.runtime.verification_runner import VerificationCommand
from fsagent.runtime.verifier import verify_execution, verify_execution_async

PROJECT_ROOT = Path(verifier_module.__file__).resolve().parents[2]


def test_verify_execution_records_skipped_reason_when_no_verification_exists():
    result = verify_execution(
        todos=[{"id": "todo-001", "content": "Inspect files", "status": "completed"}],
        execution_log=[],
    )

    assert result.status == "completed"
    assert result.verification == [
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


def test_verify_execution_failed_record_needs_revision():
    result = verify_execution(
        todos=[{"id": "todo-001", "content": "Run tests", "status": "completed"}],
        execution_log=[],
        verification=[
            {
                "id": "verification-001",
                "todo_id": "todo-001",
                "command": "pytest",
                "status": "failed",
                "exit_code": 1,
                "stderr_summary": "failed",
            }
        ],
    )

    assert result.status == "needs_revision"
    assert result.verification[0]["status"] == "failed"


def test_verify_execution_failed_todo_needs_revision_even_when_verification_is_skipped():
    result = verify_execution(
        todos=[{"id": "todo-001", "content": "Run tests", "status": "failed"}],
        execution_log=[{"id": "log-001", "todo_id": "todo-001", "content": "Run tests", "status": "failed"}],
    )

    assert result.status == "needs_revision"
    assert result.verification[0]["status"] == "skipped"
    assert result.verification[0]["reason"] == "No verification command was provided."


def test_verify_execution_passed_record_completes():
    result = verify_execution(
        todos=[{"id": "todo-001", "content": "Run tests", "status": "completed"}],
        execution_log=[],
        verification=[{"id": "verification-001", "command": "pytest", "status": "passed", "exit_code": 0}],
    )

    assert result.status == "completed"


async def test_verify_execution_async_runs_plan_verification_from_project_root():
    calls: list[VerificationCommand] = []

    async def executor(command: VerificationCommand) -> CompletedProcess[str]:
        calls.append(command)
        return CompletedProcess(args=command.command, returncode=0, stdout="passed", stderr="")

    result = await verify_execution_async(
        todos=[{"id": "todo-001", "content": "Run tests", "status": "completed"}],
        execution_log=[],
        plan_meta={"verification": ["uv run --group test pytest fsagent/tests -q"]},
        executor=executor,
    )

    assert result.status == "completed"
    assert calls == [
        VerificationCommand(command="uv run --group test pytest fsagent/tests -q", cwd=PROJECT_ROOT),
    ]
