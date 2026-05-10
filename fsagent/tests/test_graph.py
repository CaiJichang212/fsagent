import pytest
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool

from fsagent.runtime.agent_observability import AgentObservabilityMiddleware
from fsagent.runtime.graph import create_runtime


def test_create_runtime_compiles_explicit_mode_graph():
    runtime = create_runtime(model="fake:model", no_mcp=True)

    assert runtime is not None


def test_runtime_rejects_missing_explicit_mode():
    runtime = create_runtime(model="fake:model", no_mcp=True)

    with pytest.raises(ValueError, match="fast` or `plan"):
        runtime.invoke({"messages": [HumanMessage(content="hello")]})


async def test_plan_planner_emits_agent_lifecycle_events_and_installs_observability(monkeypatch):
    captured_middleware = []
    events: list[dict[str, object]] = []

    class FakePlannerAgent:
        async def ainvoke(self, _payload: object, config: object = None) -> dict[str, object]:
            assert config is not None
            return {
                "todos": [{"content": "检查日志", "status": "pending"}],
                "plan_meta": {"goal": "观察运行"},
            }

    def fake_create_deep_agent(**kwargs: object) -> FakePlannerAgent:
        captured_middleware.extend(kwargs["middleware"])
        return FakePlannerAgent()

    async def event_sink(event: dict[str, object]) -> None:
        events.append(event)

    monkeypatch.setattr("fsagent.runtime.graph.create_deep_agent", fake_create_deep_agent)
    runtime = create_runtime(model="fake:model", no_mcp=True)

    result = await runtime.ainvoke(
        {"mode": "plan", "messages": [HumanMessage(content="hello")]},
        config={"configurable": {"thread_id": "thread-1"}, "metadata": {"fsagent_event_sink": event_sink}},
    )

    assert "__interrupt__" in result
    assert any(isinstance(item, AgentObservabilityMiddleware) for item in captured_middleware)
    agent_events = [
        (event["kind"], event["agent_mode"], event["phase"])
        for event in events
        if str(event["kind"]).startswith("agent.")
    ]
    assert ("agent.started", "plan", "planner") in agent_events
    assert ("agent.completed", "plan", "planner") in agent_events


async def test_plan_planner_does_not_receive_execution_tools_before_review(monkeypatch):
    captured_tools: list[object] = []

    @tool
    def external_search(query: str) -> str:
        """Search external systems."""
        return query

    class FakePlannerAgent:
        async def ainvoke(self, _payload: object, config: object = None) -> dict[str, object]:
            assert config is not None
            return {
                "todos": [{"content": "检查日志", "status": "pending"}],
                "plan_meta": {"goal": "观察运行"},
            }

    def fake_create_deep_agent(**kwargs: object) -> FakePlannerAgent:
        captured_tools.extend(kwargs["tools"])
        return FakePlannerAgent()

    monkeypatch.setattr("fsagent.runtime.graph.create_deep_agent", fake_create_deep_agent)
    runtime = create_runtime(model="fake:model", tools=[external_search], no_mcp=True)

    result = await runtime.ainvoke(
        {"mode": "plan", "messages": [HumanMessage(content="hello")]},
        config={"configurable": {"thread_id": "thread-1"}},
    )

    assert "__interrupt__" in result
    assert captured_tools == []


async def test_plan_planner_does_not_load_mcp_tools_before_review(monkeypatch):
    class FakePlannerAgent:
        async def ainvoke(self, _payload: object, config: object = None) -> dict[str, object]:
            assert config is not None
            return {
                "todos": [{"content": "检查日志", "status": "pending"}],
                "plan_meta": {"goal": "观察运行"},
            }

    def fake_create_deep_agent(**_kwargs: object) -> FakePlannerAgent:
        return FakePlannerAgent()

    async def fail_if_mcp_loads(*_args: object, **_kwargs: object) -> object:
        msg = "Planner must not load MCP tools before plan review."
        raise AssertionError(msg)

    monkeypatch.setattr("fsagent.runtime.graph.create_deep_agent", fake_create_deep_agent)
    monkeypatch.setattr("fsagent.runtime.graph.load_runtime_mcp_tools", fail_if_mcp_loads)
    runtime = create_runtime(model="fake:model", no_mcp=False)

    result = await runtime.ainvoke(
        {"mode": "plan", "messages": [HumanMessage(content="hello")]},
        config={"configurable": {"thread_id": "thread-1"}},
    )

    assert "__interrupt__" in result
