"""Planner-visible summaries of executor capabilities."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import yaml
from deepagents.backends.protocol import BackendFactory, BackendProtocol, LsResult
from langchain_core.tools import BaseTool

from fsagent.runtime.mcp import describe_mcp_config_for_planner

DEFAULT_CAPABILITY_ITEM_LIMIT = 20


@dataclass(frozen=True, slots=True)
class PlannerCapabilitySummary:
    """Text and warnings generated for planner capability context."""

    text: str
    warnings: list[str]


@dataclass(frozen=True, slots=True)
class _CapabilitySummaryItem:
    name: str
    description: str | None = None


@dataclass(frozen=True, slots=True)
class PlannerCapabilitySummaryBuilder:
    """Build a static capability summary for the planning-only agent."""

    regular_tools: Sequence[BaseTool | Callable[..., Any] | dict[str, Any]]
    skills: Sequence[str] | None
    memory: Sequence[str] | None
    backend: BackendProtocol | BackendFactory | None
    mcp_config_path: str | None
    no_mcp: bool
    trust_project_mcp: bool | None
    item_limit: int = DEFAULT_CAPABILITY_ITEM_LIMIT

    def build(self) -> PlannerCapabilitySummary:
        """Return planner-visible executor capabilities and truncation warnings."""
        warnings: list[str] = []
        tool_descriptions = [_tool_display_item(tool) for tool in self.regular_tools]
        skill_descriptions = _skill_display_items(self.skills or [], backend=self.backend)
        memory_descriptions = [_source_display_item(item) for item in self.memory or []]
        built_in_tool_descriptions = _built_in_executor_tool_display_items()
        mcp_note = describe_mcp_config_for_planner(
            self.mcp_config_path,
            no_mcp=self.no_mcp,
            trust_project_mcp=self.trust_project_mcp,
            item_limit=self.item_limit,
            warnings=warnings,
        )
        built_in_tools = _format_capabilities(
            "Deep Agents built-in executor tools",
            built_in_tool_descriptions,
            warnings,
            self.item_limit,
        )
        caller_tools = _format_capabilities(
            "Caller-provided executor tools",
            tool_descriptions,
            warnings,
            self.item_limit,
        )
        executor_skills = _format_capabilities("Executor skills", skill_descriptions, warnings, self.item_limit)
        executor_memory = _format_capabilities(
            "Executor memory sources",
            memory_descriptions,
            warnings,
            self.item_limit,
        )

        return PlannerCapabilitySummary(
            text="\n".join(
                [
                    "Planner capability context:",
                    "- During planning you may only call `write_todos`.",
                    "- Do not call filesystem, shell, MCP, subagent, skill, or external tools before plan approval.",
                    f"- Deep Agents built-in executor tools after approval: {built_in_tools}.",
                    f"- Caller-provided executor tools after approval: {caller_tools}.",
                    f"- Executor skills after approval: {executor_skills}.",
                    f"- Executor memory sources after approval: {executor_memory}.",
                    f"- {mcp_note}",
                ]
            ),
            warnings=warnings,
        )


def _built_in_executor_tool_display_items() -> list[_CapabilitySummaryItem]:
    return [
        _CapabilitySummaryItem("ls", "List files and directories after plan approval."),
        _CapabilitySummaryItem("read_file", "Read file contents after plan approval."),
        _CapabilitySummaryItem("write_file", "Write new file contents after plan approval."),
        _CapabilitySummaryItem("edit_file", "Edit existing files after plan approval."),
        _CapabilitySummaryItem("glob", "Find files by pattern after plan approval."),
        _CapabilitySummaryItem("grep", "Search file contents after plan approval."),
        _CapabilitySummaryItem("execute", "Run backend commands when configured and after plan approval."),
        _CapabilitySummaryItem("task", "Delegate to a subagent when configured and after plan approval."),
    ]


def _tool_display_item(tool: BaseTool | Callable[..., Any] | dict[str, Any]) -> _CapabilitySummaryItem:
    if isinstance(tool, BaseTool):
        return _CapabilitySummaryItem(name=tool.name, description=_normalize_description(tool.description))
    if isinstance(tool, Mapping):
        name = tool.get("name")
        if isinstance(name, str) and name:
            return _CapabilitySummaryItem(name=name, description=_normalize_description(tool.get("description")))
    name = getattr(tool, "__name__", type(tool).__name__)
    description = getattr(tool, "__doc__", None)
    return _CapabilitySummaryItem(name=str(name), description=_normalize_description(description))


def _skill_display_items(
    skills: Sequence[str],
    *,
    backend: BackendProtocol | BackendFactory | None,
) -> list[_CapabilitySummaryItem]:
    items: list[_CapabilitySummaryItem] = []
    for source in skills:
        if isinstance(backend, BackendProtocol):
            source_items = _backend_skill_display_items(backend, source)
            if source_items:
                items.extend(source_items)
                continue
        source_path = Path(source).expanduser()
        if not source_path.is_absolute():
            source_path = Path.cwd() / source_path
        if not source_path.exists():
            items.append(_source_display_item(source))
            continue
        source_items = _local_skill_display_items(source_path)
        items.extend(source_items or [_source_display_item(source)])
    return items


def _backend_skill_display_items(backend: BackendProtocol, source: str) -> list[_CapabilitySummaryItem]:
    try:
        ls_result = backend.ls(source)
    except (NotImplementedError, OSError, RuntimeError, ValueError):
        return []
    entries = ls_result.entries if isinstance(ls_result, LsResult) else ls_result
    skill_dirs = [entry["path"] for entry in entries or [] if entry.get("is_dir") and "path" in entry]
    if not skill_dirs:
        return []
    skill_paths = [str(PurePosixPath(skill_dir) / "SKILL.md") for skill_dir in skill_dirs]
    try:
        responses = backend.download_files(skill_paths)
    except (NotImplementedError, OSError, RuntimeError, ValueError):
        return []

    items: list[_CapabilitySummaryItem] = []
    for skill_dir, _skill_path, response in zip(skill_dirs, skill_paths, responses, strict=True):
        if response.error or response.content is None:
            continue
        content = response.content.decode("utf-8", errors="replace")
        item = _skill_content_display_item(
            content,
            fallback_name=PurePosixPath(skill_dir).name,
        )
        if item is not None:
            items.append(item)
    return items


def _local_skill_display_items(source_path: Path) -> list[_CapabilitySummaryItem]:
    if not source_path.is_dir():
        return []
    items: list[_CapabilitySummaryItem] = []
    for skill_file in sorted(source_path.glob("*/SKILL.md")):
        item = _skill_file_display_item(skill_file)
        if item is not None:
            items.append(item)
    return items


def _skill_file_display_item(skill_file: Path) -> _CapabilitySummaryItem | None:
    try:
        content = skill_file.read_text(encoding="utf-8")
    except OSError:
        return None
    return _skill_content_display_item(content, fallback_name=skill_file.parent.name)


def _skill_content_display_item(
    content: str,
    *,
    fallback_name: str,
) -> _CapabilitySummaryItem | None:
    metadata = _parse_frontmatter(content)
    name = metadata.get("name") or fallback_name
    if not isinstance(name, str) or not name.strip():
        return None
    description = metadata.get("description")
    return _CapabilitySummaryItem(name=name.strip(), description=_normalize_description(description))


def _parse_frontmatter(content: str) -> dict[str, object]:
    if not content.startswith("---"):
        return {}
    parts = content.split("---", 2)
    if len(parts) < 3:  # noqa: PLR2004
        return {}
    try:
        metadata = yaml.safe_load(parts[1])
    except yaml.YAMLError:
        return {}
    return metadata if isinstance(metadata, dict) else {}


def _source_display_item(source: object) -> _CapabilitySummaryItem:
    return _CapabilitySummaryItem(name=str(source))


def _normalize_description(description: object, *, max_length: int = 240) -> str | None:
    if not isinstance(description, str):
        return None
    normalized = " ".join(description.split())
    if not normalized:
        return None
    if len(normalized) <= max_length:
        return normalized
    return f"{normalized[: max_length - 3].rstrip()}..."


def _format_capabilities(
    label: str,
    items: Sequence[_CapabilitySummaryItem],
    warnings: list[str],
    item_limit: int,
) -> str:
    if not items:
        return "none"
    visible_items = list(items[:item_limit])
    omitted_count = max(len(items) - len(visible_items), 0)
    parts = [f"{item.name} - {item.description}" if item.description else item.name for item in visible_items]
    if omitted_count:
        parts.append(f"+{omitted_count} more")
        warnings.append(f"{label} truncated to {item_limit} items; +{omitted_count} more omitted.")
    return "; ".join(parts)
