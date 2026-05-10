"""Configuration types for the fast/plan runtime."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from deepagents.backends.protocol import BackendFactory, BackendProtocol
from langchain.agents.middleware import InterruptOnConfig
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage
from langchain_core.tools import BaseTool
from langgraph.store.base import BaseStore
from langgraph.types import Checkpointer


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    """Runtime construction options."""

    model: str | BaseChatModel
    tools: Sequence[BaseTool | Callable[..., Any] | dict[str, Any]] | None = None
    system_prompt: str | SystemMessage | None = None
    skills: list[str] | None = None
    memory: list[str] | None = None
    backend: BackendProtocol | BackendFactory | None = None
    interrupt_on: dict[str, bool | InterruptOnConfig] | None = None
    checkpointer: Checkpointer | None = None
    store: BaseStore | None = None
    mcp_config_path: str | None = None
    no_mcp: bool = False
    trust_project_mcp: bool | None = None
