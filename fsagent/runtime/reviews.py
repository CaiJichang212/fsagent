"""Review payload builders for runtime approval gates."""

from collections.abc import Mapping, Sequence
from typing import Any


def build_deviation_review_payload(
    *,
    subject: str,
    proposed_input_summary: str,
    risk: str = "medium",
) -> dict[str, Any]:
    """Build the interrupt payload for an execution deviation review."""
    return {
        "kind": "deviation_review",
        "risk": risk,
        "subject": subject,
        "proposed_input_summary": proposed_input_summary,
        "allowed_actions": ["approve", "replan", "cancel"],
        "instructions": "Review the requested deviation. Approve to continue, replan to revise the plan, or cancel.",
    }


def build_mcp_review_payload(*, servers: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Build the interrupt payload for high-risk MCP server review."""
    risk = "high" if any(server.get("risk") == "high" for server in servers) else "medium"
    return {
        "kind": "mcp_review",
        "risk": risk,
        "subject": "High risk MCP servers require approval",
        "proposed_input_summary": _summarize_servers(servers),
        "allowed_actions": ["approve", "deny", "cancel"],
        "instructions": "Review the MCP servers before enabling them for execution.",
    }


def _summarize_servers(servers: Sequence[Mapping[str, Any]]) -> str:
    lines: list[str] = []
    for server in servers:
        name = str(server.get("name") or "unknown")
        transport = str(server.get("transport") or "unknown")
        risk = str(server.get("risk") or "medium")
        description = server.get("description")
        line = f"{name} ({transport}, {risk})"
        if description:
            line = f"{line} - {description}"
        lines.append(line)
    return "\n".join(lines)
