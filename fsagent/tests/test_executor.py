from fsagent.runtime.executor import execute_plan
from fsagent.runtime.prompts import EXECUTOR_SYSTEM_PROMPT


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
