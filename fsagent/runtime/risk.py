"""Shared runtime risk classification helpers."""

from __future__ import annotations

import re

_CAMEL_BOUNDARY_PATTERN = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_TOKEN_PATTERN = re.compile(r"[^A-Za-z0-9]+")
_READ_ONLY_PATTERN = re.compile(r"\b(read[- ]?only|no writes?|without side effects?)\b", re.IGNORECASE)

_HIGH_RISK_TOKENS = {
    "add",
    "bash",
    "checkout",
    "chmod",
    "click",
    "command",
    "comment",
    "commit",
    "create",
    "delete",
    "destroy",
    "edit",
    "exec",
    "execute",
    "fill",
    "grant",
    "insert",
    "merge",
    "modify",
    "move",
    "patch",
    "post",
    "publish",
    "push",
    "reboot",
    "remove",
    "rename",
    "revoke",
    "run",
    "send",
    "shell",
    "shutdown",
    "submit",
    "type",
    "update",
    "upload",
    "upsert",
    "write",
}

_READ_ONLY_TOOL_TOKENS = {
    "cat",
    "details",
    "fetch",
    "find",
    "get",
    "grep",
    "head",
    "list",
    "lookup",
    "ls",
    "open",
    "preview",
    "query",
    "read",
    "resolve",
    "rg",
    "search",
    "stat",
    "tail",
}


def is_high_risk_tool_text(*values: object) -> bool:
    """Return whether any tool name/description text suggests side effects."""
    if not values:
        return False
    name = str(values[0] or "")
    description = " ".join(str(value or "") for value in values[1:])
    name_tokens = _tokens(name)
    if name_tokens & _HIGH_RISK_TOKENS:
        return True
    if _READ_ONLY_PATTERN.search(description) and name_tokens & _READ_ONLY_TOOL_TOKENS:
        return False
    return bool(_tokens(description) & _HIGH_RISK_TOKENS)


def _tokens(value: str) -> set[str]:
    split_camel = _CAMEL_BOUNDARY_PATTERN.sub(" ", value)
    normalized = _NON_TOKEN_PATTERN.sub(" ", split_camel)
    return {token.lower() for token in normalized.split() if token}
