"""Planning-only LangChain agent construction."""

from collections.abc import Awaitable, Callable, Mapping

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware, ToolCallLimitMiddleware
from langchain.agents.middleware.todo import TodoListMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage
from langgraph.store.base import BaseStore
from langgraph.types import Checkpointer

from fsagent.runtime.agent_observability import (
    AgentLogContext,
    AgentObservabilityMiddleware,
    PlannerToolBoundaryMiddleware,
)
from fsagent.runtime.prompts import PLANNER_TODO_SYSTEM_PROMPT, PLANNER_TODO_TOOL_DESCRIPTION
from fsagent.runtime.state import RuntimeState

ProgressCallback = Callable[[Mapping[str, object]], Awaitable[None]]

PLANNER_MODEL_CALL_RUN_LIMIT = 8
PLANNER_WRITE_TODOS_RUN_LIMIT = 1


def create_planner_agent(
    *,
    model: str | BaseChatModel,
    system_prompt: str | SystemMessage,
    on_event: ProgressCallback | None,
    checkpointer: Checkpointer | None,
    store: BaseStore | None,
) -> object:
    """Create the planning-only agent used before plan review approval."""
    planner_context = AgentLogContext(agent_mode="plan", phase="planner")
    return create_agent(
        model=model,
        tools=[],
        system_prompt=system_prompt,
        middleware=[
            TodoListMiddleware(
                system_prompt=PLANNER_TODO_SYSTEM_PROMPT,
                tool_description=PLANNER_TODO_TOOL_DESCRIPTION,
            ),
            PlannerToolBoundaryMiddleware(on_event=on_event),
            ToolCallLimitMiddleware(
                tool_name="write_todos",
                run_limit=PLANNER_WRITE_TODOS_RUN_LIMIT,
                exit_behavior="error",
            ),
            ModelCallLimitMiddleware(
                run_limit=PLANNER_MODEL_CALL_RUN_LIMIT,
                exit_behavior="error",
            ),
            AgentObservabilityMiddleware(
                context=planner_context,
                on_event=on_event,
            ),
        ],
        state_schema=RuntimeState,
        checkpointer=checkpointer,
        store=store,
    )
