from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage
from langchain_core.tools import StructuredTool

from fsagent.runtime.policy import ToolPolicyMiddleware, evaluate_tool_policy, policy_interrupt_on_for_tools


async def _sample_tool() -> str:
    """Return a sample value."""
    return "ok"


def test_default_policy_allows_read_reviews_execute_and_denies_unknown():
    read_decision = evaluate_tool_policy("read_file", profile="dev-default", phase="executor")
    write_decision = evaluate_tool_policy("write_file", profile="dev-default", phase="executor")
    execute_decision = evaluate_tool_policy("execute", profile="dev-default", phase="executor")
    unknown_decision = evaluate_tool_policy("unknown_tool", profile="dev-default", phase="executor")

    assert read_decision.risk == "low"
    assert read_decision.action == "allow"
    assert write_decision.risk == "high"
    assert write_decision.action == "review"
    assert execute_decision.risk == "high"
    assert execute_decision.action == "review"
    assert unknown_decision.risk == "high"
    assert unknown_decision.action == "deny"


def test_default_policy_denies_task_outside_approved_execution_phase():
    executor_decision = evaluate_tool_policy("task", profile="dev-default", phase="executor")
    fast_decision = evaluate_tool_policy("task", profile="dev-default", phase="fast_runner")

    assert executor_decision.action == "allow"
    assert fast_decision.risk == "medium"
    assert fast_decision.action == "deny"


def test_policy_decision_reports_selected_profile():
    locked_down_decision = evaluate_tool_policy("write_file", profile="locked-down", phase="executor")
    ci_eval_decision = evaluate_tool_policy("execute", profile="ci-eval", phase="executor")

    assert locked_down_decision.profile == "locked-down"
    assert ci_eval_decision.profile == "ci-eval"


async def test_tool_policy_event_reports_selected_profile():
    events: list[dict[str, object]] = []
    tool = StructuredTool.from_function(coroutine=_sample_tool, name="write_file", description="Write file.")
    middleware = ToolPolicyMiddleware(profile="locked-down", phase="executor", on_event=events.append)
    request = ToolCallRequest(
        tool_call={"name": "write_file", "args": {"path": "out.txt"}, "id": "call-write"},
        tool=tool,
        state={"messages": []},
        runtime=None,
    )

    async def handler(_request: ToolCallRequest) -> ToolMessage:
        return ToolMessage(content="ok", tool_call_id="call-write")

    await middleware.awrap_tool_call(request, handler)

    assert events[0]["kind"] == "tool.policy_decision"
    assert events[0]["profile"] == "locked-down"


def test_policy_interrupts_are_built_from_actual_tool_names_and_merge_existing_rules():
    execute_tool = StructuredTool.from_function(coroutine=_sample_tool, name="execute", description="Run command.")
    read_tool = StructuredTool.from_function(coroutine=_sample_tool, name="read_file", description="Read file.")

    interrupt_on = policy_interrupt_on_for_tools(
        [execute_tool, read_tool],
        profile="dev-default",
        base={"write_file": {"allowed_decisions": ["approve", "reject"]}},
    )

    assert interrupt_on["execute"] == {"allowed_decisions": ["approve", "edit", "reject"]}
    assert interrupt_on["write_file"] == {"allowed_decisions": ["approve", "reject"]}
    assert "read_file" not in interrupt_on


def test_policy_interrupts_builtin_write_tools_as_high_risk():
    write_tool = StructuredTool.from_function(coroutine=_sample_tool, name="write_file", description="Write file.")
    edit_tool = StructuredTool.from_function(coroutine=_sample_tool, name="edit_file", description="Edit file.")

    interrupt_on = policy_interrupt_on_for_tools([write_tool, edit_tool], profile="dev-default", phase="executor")

    assert interrupt_on["write_file"] == {"allowed_decisions": ["approve", "edit", "reject"]}
    assert interrupt_on["edit_file"] == {"allowed_decisions": ["approve", "edit", "reject"]}


def test_policy_interrupts_only_high_risk_mcp_tools():
    news_tool = StructuredTool.from_function(
        coroutine=_sample_tool,
        name="trends-hub_get-bbc-news",
        description="Get BBC news headlines.",
        metadata={"fsagent_tool_source": "mcp", "fsagent_tool_risk": "low"},
    )
    delete_tool = StructuredTool.from_function(
        coroutine=_sample_tool,
        name="filesystem_delete_file",
        description="Delete a local file.",
        metadata={"fsagent_tool_source": "mcp", "fsagent_tool_risk": "high"},
    )

    interrupt_on = policy_interrupt_on_for_tools([news_tool, delete_tool], profile="dev-default")

    assert "trends-hub_get-bbc-news" not in interrupt_on
    assert interrupt_on["filesystem_delete_file"] == {"allowed_decisions": ["approve", "edit", "reject"]}


def test_locked_down_policy_allows_low_risk_mcp_tools():
    news_tool = StructuredTool.from_function(
        coroutine=_sample_tool,
        name="trends-hub_get-bbc-news",
        description="Get BBC news headlines.",
        metadata={"fsagent_tool_source": "mcp", "fsagent_tool_risk": "low"},
    )

    interrupt_on = policy_interrupt_on_for_tools([news_tool], profile="locked-down")

    assert "trends-hub_get-bbc-news" not in interrupt_on


def test_policy_interrupts_cannot_be_weakened_by_caller_base_config():
    execute_tool = StructuredTool.from_function(coroutine=_sample_tool, name="execute", description="Run command.")

    interrupt_on = policy_interrupt_on_for_tools(
        [execute_tool],
        profile="dev-default",
        base={"execute": False},
    )

    assert interrupt_on["execute"] == {"allowed_decisions": ["approve", "edit", "reject"]}
