"""MCP tool loading for the fast/plan runtime."""

import importlib
import json
import logging
import os
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

from anyio import Path as AsyncPath
from langchain_core.tools import BaseTool, StructuredTool

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RuntimeMCPResult:
    """Loaded MCP tools and runtime MCP metadata."""

    tools: list[BaseTool]
    client: object | None = None
    server_infos: list[object] = field(default_factory=list)


async def load_runtime_mcp_tools(
    config_path: str | None,
    *,
    no_mcp: bool = False,
    trust_project_mcp: bool | None = None,
) -> RuntimeMCPResult:
    """Load MCP tools from a config file.

    Args:
        config_path: Path to a JSON MCP config.
        no_mcp: Disable MCP loading.
        trust_project_mcp: Whether project-level stdio MCP servers are trusted.

    Returns:
        Loaded MCP tools and client metadata.
    """
    if no_mcp or config_path is None:
        return RuntimeMCPResult(tools=[])

    resolved_config_path = resolve_mcp_config_path(config_path)
    if resolved_config_path is None:
        return RuntimeMCPResult(tools=[])
    if not await AsyncPath(resolved_config_path).exists():
        msg = f"MCP config file not found: {resolved_config_path}"
        raise FileNotFoundError(msg)

    prepared = await _prepare_explicit_mcp_config_path(
        resolved_config_path,
        trust_project_mcp=trust_project_mcp,
    )
    if prepared is None:
        return RuntimeMCPResult(tools=[])

    try:
        tools, manager, server_infos = await _resolve_and_load_mcp_tools(
            explicit_config_path=prepared.path,
            no_mcp=False,
            trust_project_mcp=trust_project_mcp,
            stateless=True,
        )
    finally:
        if prepared.temporary_path is not None:
            await AsyncPath(prepared.temporary_path).unlink(missing_ok=True)

    return RuntimeMCPResult(
        tools=_make_mcp_tool_errors_nonfatal(tools),
        client=manager,
        server_infos=list(server_infos),
    )


def resolve_mcp_config_path(config_path: str | None) -> str | None:
    """Resolve an MCP config path relative to the current working directory.

    Args:
        config_path: Optional MCP config path.

    Returns:
        Absolute MCP config path, or `None` when no path was provided.
    """
    if config_path is None:
        return None
    path = Path(config_path).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return str(path.resolve(strict=False))


@dataclass(frozen=True, slots=True)
class _PreparedMCPConfig:
    path: str
    temporary_path: str | None = None


async def _resolve_and_load_mcp_tools(
    *,
    explicit_config_path: str | None,
    no_mcp: bool,
    trust_project_mcp: bool | None,
    stateless: bool,
) -> tuple[list[BaseTool], object | None, list[object]]:
    module = _import_deepagents_cli_mcp_tools()
    resolve_and_load_mcp_tools = module.resolve_and_load_mcp_tools

    return await resolve_and_load_mcp_tools(
        explicit_config_path=explicit_config_path,
        no_mcp=no_mcp,
        trust_project_mcp=trust_project_mcp,
        stateless=stateless,
    )


def _import_deepagents_cli_mcp_tools() -> ModuleType:
    try:
        return importlib.import_module("deepagents_cli.mcp_tools")
    except ModuleNotFoundError as exc:
        if exc.name != "deepagents_cli":
            raise

        source_path = _deepagents_cli_source_path()
        if source_path is not None:
            if source_path not in sys.path:
                sys.path.insert(0, source_path)
            try:
                return importlib.import_module("deepagents_cli.mcp_tools")
            except ModuleNotFoundError as retry_exc:
                if retry_exc.name != "deepagents_cli":
                    raise

        msg = (
            "MCP 已启用, 但当前 Python 环境找不到 `deepagents-cli`. "
            "请使用 `./scripts/start-dev.sh` 或 `uv run uvicorn fsagent.api.server:app "
            "--host 127.0.0.1 --port 8000 --reload --no-access-log` 启动后端, "
            "或先执行 `uv sync` 安装后端依赖."
        )
        raise RuntimeError(msg) from exc


def _deepagents_cli_source_path() -> str | None:
    project_root = Path(__file__).resolve().parents[2]
    candidate = project_root.parent.parent / "libs" / "cli"
    if (candidate / "deepagents_cli" / "mcp_tools.py").exists():
        return str(candidate)
    return None


async def _prepare_explicit_mcp_config_path(
    config_path: str,
    *,
    trust_project_mcp: bool | None,
) -> _PreparedMCPConfig | None:
    raw = json.loads(await AsyncPath(config_path).read_text(encoding="utf-8"))
    normalized = _normalize_explicit_mcp_config(raw, trust_project_mcp=trust_project_mcp)
    if not normalized["mcpServers"]:
        return None
    if raw == normalized:
        return _PreparedMCPConfig(path=config_path)

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, suffix=".json") as file_obj:
        json.dump(normalized, file_obj)
        temporary_path = file_obj.name
    return _PreparedMCPConfig(path=temporary_path, temporary_path=temporary_path)


def _normalize_explicit_mcp_config(raw: object, *, trust_project_mcp: bool | None) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, dict):
        msg = "MCP config must contain an object of servers."
        raise TypeError(msg)

    servers = raw.get("mcpServers", raw)
    if not isinstance(servers, dict):
        msg = "MCP config must contain an object of servers."
        raise TypeError(msg)

    normalized: dict[str, Any] = {}
    for name, server in servers.items():
        if not isinstance(server, dict):
            continue
        normalized_server = _normalize_explicit_mcp_server(server, trust_project_mcp=trust_project_mcp)
        if normalized_server is not None:
            normalized[str(name)] = normalized_server
    return {"mcpServers": normalized}


def _normalize_explicit_mcp_server(
    server: dict[str, Any],
    *,
    trust_project_mcp: bool | None,
) -> dict[str, Any] | None:
    if server.get("disabled") is True:
        return None

    transport = _infer_transport(server)
    if transport == "stdio":
        return _normalize_stdio_server(server, trust_project_mcp=trust_project_mcp)
    if transport in {"http", "streamable_http", "streamable-http", "sse"}:
        return dict(server)
    return None


def _make_mcp_tool_errors_nonfatal(tools: list[BaseTool]) -> list[BaseTool]:
    return [_make_mcp_tool_error_nonfatal(tool) for tool in tools]


def _make_mcp_tool_error_nonfatal(tool: BaseTool) -> BaseTool:
    async def invoke_mcp_tool(**kwargs: object) -> object:
        try:
            return await tool.ainvoke(kwargs)
        except Exception as exc:  # noqa: BLE001  # external MCP tool failures should be visible to the model
            return _format_mcp_tool_error(tool.name, exc)

    if tool.args_schema is None:
        tool.handle_tool_error = _make_mcp_tool_error_formatter(tool.name)
        return tool

    return StructuredTool.from_function(
        coroutine=invoke_mcp_tool,
        name=tool.name,
        description=tool.description,
        args_schema=tool.args_schema,
        infer_schema=False,
        metadata=tool.metadata,
        tags=tool.tags,
    )


def _make_mcp_tool_error_formatter(tool: str) -> Callable[[Exception], str]:
    def format_mcp_tool_error(error: Exception) -> str:
        return _format_mcp_tool_error(tool, error)

    return format_mcp_tool_error


def _format_mcp_tool_error(tool: str, error: Exception) -> str:
    detail = str(error) or "Tool execution error"
    return f"MCP tool `{tool}` failed: {detail}"


def _infer_transport(server: dict[str, Any]) -> object:
    transport = server.get("transport") or server.get("type")
    if transport is not None:
        return transport
    if "command" in server:
        return "stdio"
    if "url" in server:
        return "streamable_http"
    return None


def _normalize_stdio_server(
    server: dict[str, Any],
    *,
    trust_project_mcp: bool | None,
) -> dict[str, Any] | None:
    if not trust_project_mcp:
        return None
    command = server.get("command")
    if not isinstance(command, str) or not command:
        return None
    normalized = dict(server)
    normalized["type"] = "stdio"
    normalized["command"] = command
    normalized["args"] = _string_list(server.get("args", []))
    env = _stdio_env(command, server.get("env"))
    if env:
        normalized["env"] = env
    else:
        normalized.pop("env", None)
    return normalized


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _stdio_env(command: str, value: object) -> dict[str, str] | None:
    env = _string_dict(value)
    if Path(command).name != "npx":
        return env or None
    cache = str(Path(os.environ.get("TMPDIR", tempfile.gettempdir())) / "fsagent-npm-cache")
    env.setdefault("npm_config_cache", cache)
    return env


def _string_dict(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(key): str(item) for key, item in value.items()}
