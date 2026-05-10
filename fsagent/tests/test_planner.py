from langchain_core.messages import AIMessage

from fsagent.runtime.planner import generate_plan


class PlannerAgent:
    async def ainvoke(self, state, config=None):
        del config
        return {
            "messages": [*state["messages"], AIMessage(content="planned")],
            "todos": [{"content": "分析目录结构"}],
            "plan_meta": {"goal": "分析项目", "assumptions": ["Python monorepo"], "final_output_format": "markdown"},
        }


async def test_generate_plan_uses_write_todos_state_as_authority():
    plan = await generate_plan(agent=PlannerAgent(), content="分析项目")

    assert plan.todos == [{"content": "分析目录结构", "status": "pending"}]
    assert plan.plan_meta == {
        "goal": "分析项目",
        "assumptions": ["Python monorepo"],
        "final_output_format": "markdown",
    }


class RecordingRetryPlannerAgent:
    def __init__(self) -> None:
        self.prompt = ""

    async def ainvoke(self, state, config=None):
        del config
        self.prompt = state["messages"][-1].content
        return {"todos": [{"content": "拆小计划"}], "plan_meta": {"goal": "分析项目"}}


async def test_generate_plan_includes_retry_feedback():
    agent = RecordingRetryPlannerAgent()

    await generate_plan(agent=agent, content="分析项目", feedback="计划太大，请拆小一点。")

    assert "计划太大，请拆小一点。" in agent.prompt


class CompletedStatusPlannerAgent:
    async def ainvoke(self, state, config=None):
        del state, config
        return {
            "todos": [
                {"content": "检查 ls 工具", "status": "completed"},
                {"content": "检查 grep 工具", "status": "in_progress"},
            ],
            "plan_meta": {"goal": "检查工具"},
        }


async def test_generate_plan_resets_planner_todos_to_pending_for_review():
    plan = await generate_plan(agent=CompletedStatusPlannerAgent(), content="检查工具")

    assert plan.todos == [
        {"content": "检查 ls 工具", "status": "pending"},
        {"content": "检查 grep 工具", "status": "pending"},
    ]
