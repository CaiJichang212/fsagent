"""Development launcher for the fsagent API and frontend."""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class DevConfig:
    """Configuration for the local development launcher."""

    host: str = "127.0.0.1"
    api_port: int = 8000
    frontend_port: int = 5173
    skip_dependency_check: bool = False
    auto_kill: bool = False


@dataclass(frozen=True, slots=True)
class DependencyCheck:
    """Result of a dependency probe."""

    ok: bool
    message: str


def find_port_owner(port: int) -> str | None:
    """Return PIDs listening on a TCP port, if any."""
    result = subprocess.run(  # noqa: S603
        ["lsof", "-ti", f"tcp:{port}"],  # noqa: S607
        check=False,
        capture_output=True,
        text=True,
    )
    pids = result.stdout.strip()
    return pids or None


def kill_port_owner(port: int) -> bool:
    """Kill processes listening on a TCP port. Returns True if successful."""
    pids_str = find_port_owner(port)
    if not pids_str:
        return True

    pids = pids_str.split("\n")
    success = True
    for pid in pids:
        if not pid.strip():
            continue
        try:
            result = subprocess.run(  # noqa: S603
                ["kill", "-9", pid.strip()],  # noqa: S607
                check=False,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                success = False
        except OSError:
            success = False

    if success:
        time.sleep(0.5)
        return find_port_owner(port) is None
    return False


def check_backend_dependencies(root: Path) -> DependencyCheck:
    """Check whether backend dependencies can be imported through uv."""
    probe = "import fastapi; import uvicorn; import deepagents_cli; import fsagent.api.server"
    result = subprocess.run(  # noqa: S603
        ["uv", "run", "python", "-c", probe],  # noqa: S607
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return DependencyCheck(ok=True, message="Python dependencies are installed.")
    detail = (result.stderr or result.stdout).strip()
    return DependencyCheck(ok=False, message=f"Python dependencies are not ready. Run `uv sync`. {detail}")


def check_frontend_dependencies(frontend_dir: Path) -> DependencyCheck:
    """Check whether frontend npm dependencies are installed and Vite is runnable."""
    if not (frontend_dir / "node_modules").exists():
        return DependencyCheck(ok=False, message="Frontend dependencies are missing. Run `npm install` in frontend/.")
    result = subprocess.run(
        ["npm", "exec", "vite", "--", "--version"],  # noqa: S607
        cwd=frontend_dir,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return DependencyCheck(ok=True, message="Frontend dependencies are installed.")
    detail = (result.stderr or result.stdout).strip()
    return DependencyCheck(ok=False, message=f"Frontend dependencies are not ready. Run `npm install`. {detail}")


def start_commands(config: DevConfig) -> tuple[list[str], list[str]]:
    """Return backend and frontend development commands."""
    api = [
        "uv",
        "run",
        "uvicorn",
        "fsagent.api.server:app",
        "--host",
        config.host,
        "--port",
        str(config.api_port),
        "--reload",
        "--no-access-log",
    ]
    frontend = ["npm", "run", "dev", "--", "--host", config.host, "--port", str(config.frontend_port)]
    return api, frontend


def main(argv: list[str] | None = None) -> int:
    """Run preflight checks, then start backend and frontend dev servers."""
    config = _parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    frontend_dir = root / "frontend"

    failures = _preflight(root=root, frontend_dir=frontend_dir, config=config)
    if failures:
        for failure in failures:
            sys.stderr.write(f"[error] {failure}\n")
        return 1

    api_cmd, frontend_cmd = start_commands(config)
    env = os.environ.copy()
    env["FSAGENT_API_TARGET"] = f"http://{config.host}:{config.api_port}"
    sys.stdout.write(f"[start] API      http://{config.host}:{config.api_port}\n")
    sys.stdout.write(f"[start] Frontend http://{config.host}:{config.frontend_port}\n")
    api_proc = subprocess.Popen(api_cmd, cwd=root, env=env)  # noqa: S603
    frontend_proc = subprocess.Popen(frontend_cmd, cwd=frontend_dir, env=env)  # noqa: S603
    processes = [api_proc, frontend_proc]

    def stop(_signum: int, _frame: object) -> None:
        _terminate(processes)

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    try:
        while True:
            for proc in processes:
                if proc.poll() is not None:
                    _terminate([item for item in processes if item is not proc])
                    return proc.returncode or 0
            time.sleep(0.5)
    finally:
        _terminate(processes)


def _parse_args(argv: list[str] | None) -> DevConfig:
    parser = argparse.ArgumentParser(description="Start fsagent API and frontend dev servers.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--api-port", type=int, default=8000)
    parser.add_argument("--frontend-port", type=int, default=5173)
    parser.add_argument("--skip-deps", action="store_true", help="Skip dependency checks.")
    parser.add_argument("--auto-kill", action="store_true", help="Automatically kill processes using required ports.")
    args = parser.parse_args(argv)
    return DevConfig(
        host=args.host,
        api_port=args.api_port,
        frontend_port=args.frontend_port,
        skip_dependency_check=args.skip_deps,
        auto_kill=args.auto_kill,
    )


def _preflight(root: Path, frontend_dir: Path, config: DevConfig) -> list[str]:
    failures = []
    for label, port in (("API", config.api_port), ("Frontend", config.frontend_port)):
        owner = find_port_owner(port)
        if owner:
            if config.auto_kill:
                sys.stdout.write(f"[kill] {label} port {port} is in use by PID(s): {owner}, terminating...\n")
                if kill_port_owner(port):
                    sys.stdout.write(f"[kill] Successfully terminated process(es) on {label} port {port}\n")
                else:
                    failures.append(f"{label} port {port} is still in use after kill attempt")
            else:
                failures.append(f"{label} port {port} is already in use by PID(s): {owner}")

    if not config.skip_dependency_check:
        for check in (check_backend_dependencies(root), check_frontend_dependencies(frontend_dir)):
            sys.stdout.write(f"[check] {check.message}\n")
            if not check.ok:
                failures.append(check.message)
    return failures


def _terminate(processes: list[subprocess.Popen[object]]) -> None:
    for proc in processes:
        if proc.poll() is None:
            proc.terminate()
    for proc in processes:
        if proc.poll() is None:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
