import json
import sys
from types import ModuleType

from anyio import Path as AsyncPath
from langchain_core.tools import StructuredTool
from langchain_core.tools.base import ToolException

from fsagent.runtime import mcp
from fsagent.runtime.mcp import load_runtime_mcp_tools, resolve_mcp_config_path, summarize_mcp_servers_for_review


async def test_load_runtime_mcp_tools_delegates_to_deepagents_cli_loader(monkeypatch, tmp_path):
    config = tmp_path / "mcp.json"
    config.write_text(json.dumps({"mcpServers": {"docs": {"url": "https://example.test/mcp"}}}))
    captured: dict[str, object] = {}

    async def fake_resolve_and_load_mcp_tools(
        **kwargs: object,
    ) -> tuple[list[StructuredTool], str, list[str]]:
        captured.update(kwargs)
        return [], "manager", ["docs"]

    monkeypatch.setattr(mcp, "_resolve_and_load_mcp_tools", fake_resolve_and_load_mcp_tools)

    result = await load_runtime_mcp_tools(str(config), trust_project_mcp=True)

    assert captured == {
        "explicit_config_path": str(config.resolve()),
        "no_mcp": False,
        "trust_project_mcp": True,
        "stateless": True,
    }
    assert result.tools == []
    assert result.client == "manager"
    assert result.server_infos == ["docs"]


def test_import_deepagents_cli_mcp_tools_falls_back_to_repo_source(monkeypatch):
    imported: list[str] = []
    fake_module = ModuleType("deepagents_cli.mcp_tools")

    def fake_import_module(name: str) -> object:
        imported.append(name)
        if len(imported) == 1:
            raise ModuleNotFoundError(name="deepagents_cli")
        return fake_module

    monkeypatch.setattr(mcp.importlib, "import_module", fake_import_module)
    monkeypatch.setattr(mcp, "_deepagents_cli_source_path", lambda: "/repo/libs/cli")
    monkeypatch.setattr(sys, "path", [item for item in sys.path if item != "/repo/libs/cli"])

    result = mcp._import_deepagents_cli_mcp_tools()  # noqa: SLF001

    assert result is fake_module
    assert imported == ["deepagents_cli.mcp_tools", "deepagents_cli.mcp_tools"]
    assert sys.path[0] == "/repo/libs/cli"


def test_import_deepagents_mcp_tools_uses_code_module_when_cli_module_is_missing(monkeypatch):
    imported: list[str] = []
    fake_module = ModuleType("deepagents_code.mcp_tools")

    def fake_import_module(name: str) -> object:
        imported.append(name)
        if name == "deepagents_cli.mcp_tools":
            raise ModuleNotFoundError(name="deepagents_cli.mcp_tools")
        return fake_module

    monkeypatch.setattr(mcp.importlib, "import_module", fake_import_module)

    result = mcp._import_deepagents_cli_mcp_tools()  # noqa: SLF001

    assert result is fake_module
    assert imported == ["deepagents_cli.mcp_tools", "deepagents_code.mcp_tools"]


async def test_load_runtime_mcp_tools_honors_no_mcp(tmp_path):
    config = tmp_path / "mcp.json"
    config.write_text(json.dumps({"mcpServers": {"docs": {"type": "http", "url": "https://example.test/mcp"}}}))

    result = await load_runtime_mcp_tools(str(config), no_mcp=True)

    assert result.tools == []
    assert result.client is None


async def test_load_runtime_mcp_tools_skips_project_stdio_without_trust(tmp_path):
    config = tmp_path / "mcp.json"
    config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "local": {
                        "type": "stdio",
                        "command": "python",
                        "args": ["server.py"],
                    }
                }
            }
        )
    )

    result = await load_runtime_mcp_tools(str(config), trust_project_mcp=False)

    assert result.tools == []
    assert result.client is None


async def test_load_runtime_mcp_tools_infers_vscode_style_transports(monkeypatch, tmp_path):
    config = tmp_path / "mcp.json"
    config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "local": {
                        "command": "python",
                        "args": ["server.py"],
                    },
                    "docs": {
                        "url": "https://example.test/mcp",
                        "headers": {"Authorization": "Bearer token"},
                    },
                    "disabled": {
                        "command": "python",
                        "args": ["disabled.py"],
                        "disabled": True,
                    },
                }
            }
        )
    )
    captured: dict[str, object] = {}

    async def fake_resolve_and_load_mcp_tools(
        **kwargs: object,
    ) -> tuple[list[StructuredTool], str, list[str]]:
        config_path = str(kwargs["explicit_config_path"])
        captured["config"] = json.loads(await AsyncPath(config_path).read_text(encoding="utf-8"))
        return [], "manager", []

    monkeypatch.setattr(mcp, "_resolve_and_load_mcp_tools", fake_resolve_and_load_mcp_tools)

    result = await load_runtime_mcp_tools(str(config), trust_project_mcp=True)

    connections = captured["config"]["mcpServers"]
    assert result.client is not None
    assert set(connections) == {"local", "docs"}
    assert connections["local"]["type"] == "stdio"
    assert connections["docs"]["url"] == "https://example.test/mcp"


async def test_load_runtime_mcp_tools_returns_tool_errors_to_model(monkeypatch, tmp_path):
    config = tmp_path / "mcp.json"
    config.write_text(json.dumps({"mcpServers": {"docs": {"url": "https://example.test/mcp"}}}))

    async def forbidden_tool() -> str:
        """Return a forbidden response."""
        msg = "Request failed with status code 403"
        raise ToolException(msg)

    tool = StructuredTool.from_function(
        coroutine=forbidden_tool,
        name="forbidden_tool",
        description="Forbidden test tool.",
    )

    async def fake_resolve_and_load_mcp_tools(
        **kwargs: object,
    ) -> tuple[list[StructuredTool], str, list[str]]:
        del kwargs
        return [tool], "manager", []

    monkeypatch.setattr(mcp, "_resolve_and_load_mcp_tools", fake_resolve_and_load_mcp_tools)

    result = await load_runtime_mcp_tools(str(config))

    assert result.tools[0].metadata["fsagent_tool_source"] == "mcp"
    assert result.tools[0].metadata["fsagent_tool_risk"] == "low"
    output = await result.tools[0].ainvoke({})
    assert "forbidden_tool" in output
    assert "403" in output


async def test_load_runtime_mcp_tools_infers_read_only_and_destructive_tool_risk(monkeypatch, tmp_path):
    config = tmp_path / "mcp.json"
    config.write_text(json.dumps({"mcpServers": {"trends": {"command": "npx", "args": ["trends-hub"]}}}))

    async def sample_tool() -> str:
        """Return a sample response."""
        return "ok"

    news_tool = StructuredTool.from_function(
        coroutine=sample_tool,
        name="trends-hub_get-bbc-news",
        description="Get BBC news headlines.",
    )
    delete_tool = StructuredTool.from_function(
        coroutine=sample_tool,
        name="filesystem_delete_file",
        description="Delete a local file.",
    )
    rename_tool = StructuredTool.from_function(
        coroutine=sample_tool,
        name="filesystem_rename",
        description="Rename a local file.",
    )
    send_tool = StructuredTool.from_function(
        coroutine=sample_tool,
        name="email_send",
        description="Send email.",
    )

    async def fake_resolve_and_load_mcp_tools(
        **kwargs: object,
    ) -> tuple[list[StructuredTool], str, list[str]]:
        del kwargs
        return [news_tool, delete_tool, rename_tool, send_tool], "manager", []

    monkeypatch.setattr(mcp, "_resolve_and_load_mcp_tools", fake_resolve_and_load_mcp_tools)

    result = await load_runtime_mcp_tools(str(config), trust_project_mcp=True)

    risks = {tool.name: tool.metadata["fsagent_tool_risk"] for tool in result.tools}
    assert risks == {
        "trends-hub_get-bbc-news": "low",
        "filesystem_delete_file": "high",
        "filesystem_rename": "high",
        "email_send": "high",
    }


async def test_load_runtime_mcp_tools_keeps_search_tools_with_schema_sort_fields_low_risk(monkeypatch, tmp_path):
    config = tmp_path / "mcp.json"
    config.write_text(json.dumps({"mcpServers": {"hf-mcp": {"url": "https://example.test/mcp"}}}))

    async def sample_tool() -> str:
        """Return a sample response."""
        return "ok"

    search_tool = StructuredTool.from_function(
        coroutine=sample_tool,
        name="hf-mcp_hub_repo_search",
        description=(
            "Search Hugging Face repositories. Sort order descending: "
            "trendingScore, downloads, likes, createdAt, lastModified."
        ),
    )

    async def fake_resolve_and_load_mcp_tools(
        **kwargs: object,
    ) -> tuple[list[StructuredTool], str, list[str]]:
        del kwargs
        return [search_tool], "manager", []

    monkeypatch.setattr(mcp, "_resolve_and_load_mcp_tools", fake_resolve_and_load_mcp_tools)

    result = await load_runtime_mcp_tools(str(config), trust_project_mcp=True)

    assert result.tools[0].metadata["fsagent_tool_source"] == "mcp"
    assert result.tools[0].metadata["fsagent_tool_risk"] == "low"


async def test_load_runtime_mcp_tools_returns_unexpected_tool_errors_to_model(monkeypatch, tmp_path):
    config = tmp_path / "mcp.json"
    config.write_text(json.dumps({"mcpServers": {"github": {"url": "https://example.test/mcp"}}}))

    async def bad_credentials_tool() -> str:
        """Return an auth failure."""
        msg = "Authentication Failed: Bad credentials"
        raise RuntimeError(msg)

    tool = StructuredTool.from_function(
        coroutine=bad_credentials_tool,
        name="github_search",
        description="GitHub test tool.",
    )

    async def fake_resolve_and_load_mcp_tools(
        **kwargs: object,
    ) -> tuple[list[StructuredTool], str, list[str]]:
        del kwargs
        return [tool], "manager", []

    monkeypatch.setattr(mcp, "_resolve_and_load_mcp_tools", fake_resolve_and_load_mcp_tools)

    result = await load_runtime_mcp_tools(str(config))

    output = await result.tools[0].ainvoke({})
    assert "github_search" in output
    assert "Bad credentials" in output


async def test_load_runtime_mcp_tools_skips_servers_that_fail_to_list_tools(monkeypatch, tmp_path):
    config = tmp_path / "mcp.json"
    config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "broken": {"url": "https://broken.example.test/mcp"},
                    "working": {"url": "https://working.example.test/mcp"},
                }
            }
        )
    )

    async def working_tool() -> str:
        """Return a working response."""
        return "ok"

    tool = StructuredTool.from_function(
        coroutine=working_tool,
        name="working_tool",
        description="Working test tool.",
    )

    async def fake_resolve_and_load_mcp_tools(
        **kwargs: object,
    ) -> tuple[list[StructuredTool], str, list[str]]:
        del kwargs
        return [tool], "manager", ["broken", "working"]

    monkeypatch.setattr(mcp, "_resolve_and_load_mcp_tools", fake_resolve_and_load_mcp_tools)

    result = await load_runtime_mcp_tools(str(config))

    assert [tool.name for tool in result.tools] == ["working_tool"]
    assert result.server_infos == ["broken", "working"]


def test_normalize_stdio_connection_sets_writable_npm_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("TMPDIR", str(tmp_path))

    result = mcp._normalize_stdio_server(  # noqa: SLF001  # test normalization details without opening an MCP process
        {"command": "npx", "args": ["-y", "server"]},
        trust_project_mcp=True,
    )

    assert result is not None
    assert result["env"]["npm_config_cache"] == str(tmp_path / "fsagent-npm-cache")


def test_resolve_mcp_config_path_captures_relative_path_at_call_time(monkeypatch, tmp_path):
    config = tmp_path / "mcp.json"
    config.write_text("{}", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    path = resolve_mcp_config_path("mcp.json")

    monkeypatch.chdir(tmp_path.parent)
    assert path == str(config.resolve())


def test_summarize_mcp_servers_for_review_classifies_static_risk(tmp_path):
    config = tmp_path / "mcp.json"
    config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "local": {
                        "command": "python",
                        "args": ["server.py"],
                        "description": "Local server.",
                    },
                    "docs": {
                        "url": "https://example.test/mcp",
                        "description": "Docs server.",
                    },
                    "disabled": {
                        "command": "python",
                        "args": ["disabled.py"],
                        "disabled": True,
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    result = summarize_mcp_servers_for_review(str(config), no_mcp=False, trust_project_mcp=True)

    assert result == [
        {"name": "docs", "transport": "streamable_http", "risk": "medium", "description": "Docs server."},
        {"name": "local", "transport": "stdio", "risk": "high", "description": "Local server."},
    ]


def test_summarize_mcp_servers_for_review_returns_empty_for_disabled_cases(tmp_path):
    missing = tmp_path / "missing.json"
    config = tmp_path / "empty.json"
    config.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")

    assert summarize_mcp_servers_for_review(str(config), no_mcp=True, trust_project_mcp=True) == []
    assert summarize_mcp_servers_for_review(str(missing), no_mcp=False, trust_project_mcp=True) == []
    assert summarize_mcp_servers_for_review(str(config), no_mcp=False, trust_project_mcp=True) == []


def test_summarize_mcp_servers_for_review_returns_empty_for_malformed_config(tmp_path):
    config = tmp_path / "malformed.json"
    config.write_text("{not valid json", encoding="utf-8")

    assert summarize_mcp_servers_for_review(str(config), no_mcp=False, trust_project_mcp=True) == []
