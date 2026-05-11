"""Tool metadata helpers used by fsagent policy and audit layers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from langchain_core.tools import BaseTool

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(frozen=True, slots=True)
class ToolMetadata:
    """Normalized metadata for a tool visible to fsagent policy."""

    name: str
    source: str = "runtime"
    risk: str | None = None
    side_effects: list[str] = field(default_factory=list)


def tool_metadata(tool: BaseTool | Callable[..., Any] | dict[str, Any]) -> ToolMetadata:
    """Return fsagent metadata for a LangChain or callable tool."""
    name = tool_name(tool)
    raw_metadata = getattr(tool, "metadata", None)
    metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
    return ToolMetadata(
        name=name,
        source=str(metadata.get("fsagent_tool_source") or "runtime"),
        risk=str(metadata["fsagent_tool_risk"]) if metadata.get("fsagent_tool_risk") else None,
        side_effects=_string_list(metadata.get("fsagent_side_effects")),
    )


def tool_name(tool: BaseTool | Callable[..., Any] | dict[str, Any]) -> str:
    """Return a stable display name for a supported tool object."""
    if isinstance(tool, BaseTool):
        return str(tool.name)
    if isinstance(tool, dict):
        return str(tool.get("name") or "unknown")
    return str(getattr(tool, "__name__", "unknown"))


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]
