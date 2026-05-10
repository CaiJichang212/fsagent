"""Fast/plan runtime extension for Deep Agents."""

from fsagent.slash_router import RoutedRequest, parse_slash_mode

__all__ = ["RoutedRequest", "create_runtime", "parse_slash_mode"]


def __getattr__(name: str) -> object:
    """Lazily expose graph construction without slowing simple CLI commands."""
    if name == "create_runtime":
        from fsagent.runtime.graph import create_runtime  # noqa: PLC0415

        return create_runtime
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)
