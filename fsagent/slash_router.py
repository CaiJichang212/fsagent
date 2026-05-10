"""Slash command routing for explicit runtime modes."""

from dataclasses import dataclass
from typing import Literal

RuntimeMode = Literal["fast", "plan"]


@dataclass(frozen=True, slots=True)
class RoutedRequest:
    """Request routed from a slash mode prefix.

    Args:
        mode: Runtime mode selected by the slash command.
        content: User task content after removing the slash prefix.
    """

    mode: RuntimeMode
    content: str


def parse_slash_mode(text: str) -> RoutedRequest:
    """Parse a user message into an explicit runtime mode.

    Args:
        text: Raw user input.

    Returns:
        Routed request containing the selected mode and stripped content.

    Raises:
        ValueError: If the input does not start with `/fast` or `/plan`.
    """
    value = text.strip()
    for prefix, mode in (("/fast", "fast"), ("/plan", "plan")):
        if value.startswith(f"{prefix} "):
            content = value[len(prefix) :].strip()
            if content:
                return RoutedRequest(mode=mode, content=content)
    msg = "Please start your request with /fast or /plan."
    raise ValueError(msg)
