import pytest
from langgraph.errors import GraphInterrupt

from fsagent.runtime.executor import execute_plan
from fsagent.runtime.prompts import EXECUTOR_SYSTEM_PROMPT

INTERRUPT_MESSAGE = "Tool execution requires approval"


class RecordingAgent:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self.states: list[dict[str, object]] = []
        self.configs: list[dict[str, object] | None] = []

    async def ainvoke(self, state, config=None):
        self.messages.append(state["messages"][-1].content)
        self.states.append(state)
        self.configs.append(config)
        return {"messages": [*state["messages"], state["messages"][-1]], "final_response": "done"}


class InterruptingAgent:
    async def ainvoke(self, _state, **_kwargs: object):
        raise GraphInterrupt(INTERRUPT_MESSAGE)


async def test_execute_plan_records_precompleted_items_as_skipped():
    agent = RecordingAgent()

    result = await execute_plan(
        agent=agent,
        todos=[
            {"content": "已完成事项", "status": "completed"},
            {"content": "待执行事项", "status": "pending"},
        ],
        plan_meta={"goal": "测试执行"},
        config={"configurable": {"thread_id": "t1"}},
    )

    assert len(agent.messages) == 1
    assert "待执行事项" in agent.messages[0]
    assert [(todo["content"], todo["status"]) for todo in result.todos] == [
        ("已完成事项", "completed"),
        ("待执行事项", "completed"),
    ]
    assert result.execution_log[0]["content"] == "已完成事项"
    assert result.execution_log[0]["status"] == "skipped"
    assert result.execution_log[1]["result"] == "done"


async def test_execute_plan_propagates_hitl_interrupts():
    with pytest.raises(GraphInterrupt, match=INTERRUPT_MESSAGE):
        await execute_plan(
            agent=InterruptingAgent(),
            todos=[{"content": "测试 GitHub MCP 工具", "status": "pending"}],
        )


async def test_execute_plan_emits_progress_events():
    agent = RecordingAgent()
    events: list[dict[str, object]] = []

    async def on_event(event: dict[str, object]) -> None:
        events.append(event)

    await execute_plan(
        agent=agent,
        todos=[{"content": "运行联调", "status": "pending"}],
        on_event=on_event,
    )

    assert events[0]["kind"] == "todo.started"
    assert events[0]["todos"][0]["content"] == "运行联调"
    assert events[0]["todos"][0]["status"] == "in_progress"
    assert events[1]["kind"] == "todo.completed"
    assert events[1]["execution_log"][0]["content"] == "运行联调"
    assert events[1]["execution_log"][0]["status"] == "completed"
    assert events[1]["execution_log"][0]["result"] == "done"


async def test_execute_plan_todo_progress_events_include_stable_identity():
    agent = RecordingAgent()
    events: list[dict[str, object]] = []

    async def on_event(event: dict[str, object]) -> None:
        events.append(event)

    await execute_plan(
        agent=agent,
        todos=[
            {"content": "测试docs-langchain工具的基本功能", "status": "pending"},
            {"content": "测试hf-mcp工具的基本功能", "status": "pending"},
        ],
        on_event=on_event,
    )

    todo_events = [event for event in events if str(event["kind"]).startswith("todo.")]

    assert [(event["kind"], event["todo_id"], event["todo_index"], event["todo_content"]) for event in todo_events] == [
        ("todo.started", "todo-001", 1, "测试docs-langchain工具的基本功能"),
        ("todo.completed", "todo-001", 1, "测试docs-langchain工具的基本功能"),
        ("todo.started", "todo-002", 2, "测试hf-mcp工具的基本功能"),
        ("todo.completed", "todo-002", 2, "测试hf-mcp工具的基本功能"),
    ]


async def test_execute_plan_invokes_agent_with_only_current_todo_state():
    agent = RecordingAgent()

    await execute_plan(
        agent=agent,
        todos=[
            {"content": "第一项", "status": "pending"},
            {"content": "第二项", "status": "pending"},
        ],
    )

    assert [[(todo["content"], todo["status"]) for todo in state["todos"]] for state in agent.states] == [
        [("第一项", "in_progress")],
        [("第二项", "in_progress")],
    ]
    assert [(state["fsagent_todo_index"], state["fsagent_todo_content"]) for state in agent.states] == [
        (1, "第一项"),
        (2, "第二项"),
    ]
    assert "第二项" not in agent.messages[0]
    assert "第一项" not in agent.messages[1]


async def test_execute_plan_prompt_does_not_tell_executor_to_update_todos():
    agent = RecordingAgent()

    await execute_plan(
        agent=agent,
        todos=[{"content": "实现功能", "status": "pending"}],
    )

    assert "write_todos" not in agent.messages[0]
    assert "todo state" not in agent.messages[0].lower()


def test_executor_system_prompt_does_not_tell_executor_to_update_todos():
    assert "write_todos" not in EXECUTOR_SYSTEM_PROMPT
    assert "Update todo status" not in EXECUTOR_SYSTEM_PROMPT


async def test_execute_plan_uses_separate_thread_id_per_todo():
    agent = RecordingAgent()

    await execute_plan(
        agent=agent,
        todos=[
            {"content": "第一项", "status": "pending"},
            {"content": "第二项", "status": "pending"},
        ],
        config={"configurable": {"thread_id": "run-thread"}, "metadata": {"trace": "yes"}},
    )

    assert [config["configurable"]["thread_id"] for config in agent.configs] == [
        "run-thread:todo:1",
        "run-thread:todo:2",
    ]
    assert [config["metadata"] for config in agent.configs] == [{"trace": "yes"}, {"trace": "yes"}]


async def test_execute_plan_links_todo_execution_log_and_evidence_ids():
    agent = RecordingAgent()

    result = await execute_plan(
        agent=agent,
        todos=[{"content": "运行测试", "status": "pending"}],
    )

    assert result.todos[0]["id"] == "todo-001"
    assert result.todos[0]["evidence_ids"] == ["evidence-001"]
    assert result.execution_log[0]["id"] == "log-001"
    assert result.execution_log[0]["todo_id"] == "todo-001"
    assert result.evidence == [
        {
            "id": "evidence-001",
            "todo_id": "todo-001",
            "tool_call_id": None,
            "artifact_id": None,
            "summary": "done",
            "source": "runtime",
            "created_at": None,
        }
    ]


class DeviationAgent(RecordingAgent):
    async def ainvoke(self, state, config=None):
        self.messages.append(state["messages"][-1].content)
        self.states.append(state)
        self.configs.append(config)
        return {
            "final_response": "need review",
            "deviation_requested": True,
            "deviation_reason": "Need to inspect logs before editing config.",
        }


async def test_execute_plan_preserves_explicit_deviation_marker():
    agent = DeviationAgent()

    result = await execute_plan(
        agent=agent,
        todos=[{"content": "更新配置", "status": "pending"}],
    )

    assert result.final_result == "need review"
    assert result.deviation_requested is True
    assert result.deviation_reason == "Need to inspect logs before editing config."


class NonBooleanDeviationAgent(RecordingAgent):
    def __init__(self, marker: object) -> None:
        super().__init__()
        self.marker = marker

    async def ainvoke(self, state, config=None):
        self.messages.append(state["messages"][-1].content)
        self.states.append(state)
        self.configs.append(config)
        return {
            "final_response": "truthy marker",
            "deviation_requested": self.marker,
            "deviation_reason": "Truthy non-boolean marker must be ignored.",
        }


async def test_execute_plan_ignores_truthy_non_boolean_deviation_markers():
    for marker in (1, "true", "false"):
        result = await execute_plan(
            agent=NonBooleanDeviationAgent(marker),
            todos=[{"content": "检查 marker", "status": "pending"}],
        )

        assert result.deviation_requested is False
        assert result.deviation_reason is None


class MultipleDeviationAgent(RecordingAgent):
    def __init__(self) -> None:
        super().__init__()
        self.results = iter(
            [
                {
                    "final_response": "first",
                    "deviation_requested": True,
                    "deviation_reason": "First reason.",
                },
                {
                    "final_response": "second",
                    "deviation_requested": True,
                    "deviation_reason": "Second reason.",
                },
            ]
        )

    async def ainvoke(self, state, config=None):
        self.messages.append(state["messages"][-1].content)
        self.states.append(state)
        self.configs.append(config)
        return next(self.results)


async def test_execute_plan_preserves_first_non_empty_deviation_reason():
    result = await execute_plan(
        agent=MultipleDeviationAgent(),
        todos=[
            {"content": "第一项", "status": "pending"},
            {"content": "第二项", "status": "pending"},
        ],
    )

    assert result.deviation_requested is True
    assert result.deviation_reason == "First reason."
