from pathlib import Path
from subprocess import CompletedProcess
from types import SimpleNamespace

from fsagent.dev import (
    DependencyCheck,
    DevConfig,
    check_backend_dependencies,
    check_frontend_dependencies,
    find_port_owner,
    start_commands,
)


def test_find_port_owner_returns_none_for_free_port(monkeypatch):
    def fake_run(_cmd: list[str], **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(stdout="", returncode=1)

    monkeypatch.setattr("fsagent.dev.subprocess.run", fake_run)

    assert find_port_owner(5173) is None


def test_find_port_owner_returns_pid_for_occupied_port(monkeypatch):
    def fake_run(_cmd: list[str], **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(stdout="12345\n", returncode=0)

    monkeypatch.setattr("fsagent.dev.subprocess.run", fake_run)

    assert find_port_owner(8000) == "12345"


def test_backend_dependency_check_runs_uv_import_probe(monkeypatch):
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> CompletedProcess[str]:
        calls.append((cmd, kwargs))
        return CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("fsagent.dev.subprocess.run", fake_run)

    result = check_backend_dependencies(Path("/repo"))

    assert result == DependencyCheck(ok=True, message="Python dependencies are installed.")
    assert calls[0][0][:3] == ["uv", "run", "python"]
    assert "import fastapi" in calls[0][0][-1]
    assert "import deepagents_cli" in calls[0][0][-1]


def test_frontend_dependency_check_requires_node_modules_and_vite(monkeypatch):
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_exists(self: Path) -> bool:
        return str(self).endswith("node_modules")

    def fake_run(cmd: list[str], **kwargs: object) -> CompletedProcess[str]:
        calls.append((cmd, kwargs))
        return CompletedProcess(cmd, 0, stdout="vite/6.3.5", stderr="")

    monkeypatch.setattr(Path, "exists", fake_exists)
    monkeypatch.setattr("fsagent.dev.subprocess.run", fake_run)

    result = check_frontend_dependencies(Path("/repo/frontend"))

    assert result == DependencyCheck(ok=True, message="Frontend dependencies are installed.")
    assert calls[0][0] == ["npm", "exec", "vite", "--", "--version"]


def test_start_commands_use_configured_ports():
    config = DevConfig(host="127.0.0.1", api_port=9000, frontend_port=3000)

    api_cmd, frontend_cmd = start_commands(config)

    assert api_cmd == [
        "uv",
        "run",
        "uvicorn",
        "fsagent.api.server:app",
        "--host",
        "127.0.0.1",
        "--port",
        "9000",
        "--reload",
        "--no-access-log",
    ]
    assert frontend_cmd == ["npm", "run", "dev", "--", "--host", "127.0.0.1", "--port", "3000"]


def test_start_dev_script_exports_default_log_environment():
    script = Path("scripts/start-dev.sh").read_text(encoding="utf-8")

    assert 'export FSAGENT_LOG_LEVEL="${FSAGENT_LOG_LEVEL:-DEBUG}"' in script
    assert 'export FSAGENT_LOG_FILE="${FSAGENT_LOG_FILE:-logs/fsagent-api.jsonl}"' in script
