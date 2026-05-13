"""Planner context assembly with source and trust metadata."""

from dataclasses import dataclass
from typing import Literal

TrustLevel = Literal["trusted", "untrusted", "runtime"]
_DEFAULT_MAX_CHARS = 12_000
_INJECTION_PATTERNS = ("ignore all prior", "ignore previous")


@dataclass(frozen=True, slots=True)
class ContextItem:
    """One planner context item with provenance metadata."""

    source: str
    trust_level: TrustLevel
    content: str


@dataclass(frozen=True, slots=True)
class ContextBundle:
    """Planner context and runtime warnings."""

    items: list[ContextItem]
    warnings: list[str]


def build_context_bundle(
    *,
    user_message: str,
    repo_rules: str | None = None,
    max_chars: int = _DEFAULT_MAX_CHARS,
) -> ContextBundle:
    """Build planner context with stable source and trust annotations."""
    user_content = _normalize_content(user_message, max_chars=max_chars)
    items = [ContextItem(source="user", trust_level="untrusted", content=user_content)]
    warnings = _untrusted_warnings(user_content, source="user")

    if repo_rules is not None:
        items.append(
            ContextItem(
                source="repo_rules",
                trust_level="trusted",
                content=_normalize_content(repo_rules, max_chars=max_chars),
            )
        )

    return ContextBundle(items=items, warnings=warnings)


def format_context_for_planner(bundle: ContextBundle) -> str:
    """Format context while preserving source and trust metadata."""
    return "\n\n".join(f"[{item.trust_level}:{item.source}]\n{item.content}" for item in bundle.items)


def _normalize_content(content: str, *, max_chars: int) -> str:
    normalized = " ".join(content.split())
    if max_chars < 0:
        msg = "max_chars must be non-negative."
        raise ValueError(msg)
    if len(normalized) <= max_chars:
        return normalized
    return f"{normalized[:max_chars].rstrip()}... [truncated]"


def _untrusted_warnings(content: str, *, source: str) -> list[str]:
    lowered = content.lower()
    if not any(pattern in lowered for pattern in _INJECTION_PATTERNS):
        return []
    return [f"Untrusted context from {source} contains prompt-injection language."]
