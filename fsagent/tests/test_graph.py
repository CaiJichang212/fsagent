import json
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from deepagents.backends.protocol import BackendProtocol, FileDownloadResponse, LsResult
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import LanguageModelInput
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool, tool
from pydantic import Field

from fsagent.api._langgraph_compat import Command, InMemorySaver
from fsagent.runtime.graph import _planner_repo_rules, create_runtime
from fsagent.runtime.mcp import RuntimeMCPResult
from fsagent.runtime.reviews import build_deviation_review_payload, build_mcp_review_payload


def test_build_deviation_review_payload_matches_review_contract():
    payload = build_deviation_review_payload(
        subject="Executor requested plan deviation",
        proposed_input_summary="Need to inspect logs before editing config.",
    )

    assert payload == {
        "kind": "deviation_review",
        "risk": "medium",
        "subject": {
            "source": "executor",
            "summary": "Executor requested plan deviation",
            "reason": "Need to inspect logs before editing config.",
        },
        "proposed_input_summary": "Need to inspect logs before editing config.",
        "allowed_actions": ["approve", "replan", "cancel"],
        "instructions": "Review the requested deviation. Approve to continue, replan to revise the plan, or cancel.",
    }


def test_build_mcp_review_payload_summarizes_servers_for_review():
    payload = build_mcp_review_payload(
        servers=[
            {
                "name": "local-files",
                "transport": "stdio",
                "risk": "high",
                "description": "Local filesystem MCP.",
            }
        ]
    )

    assert payload == {
        "kind": "mcp_review",
        "risk": "high",
        "subject": {
            "servers": [
                {
                    "name": "local-files",
                    "transport": "stdio",
                    "risk": "high",
                    "description": "Local filesystem MCP.",
                }
            ],
        },
        "proposed_input_summary": "local-files (stdio, high) - Local filesystem MCP.",
        "allowed_actions": ["approve", "deny", "cancel"],
        "instructions": "Review the MCP servers before enabling them for execution.",
    }


def test_create_runtime_compiles_explicit_mode_graph():
    runtime = create_runtime(model="fake:model", no_mcp=True)

    assert runtime is not None


def test_runtime_rejects_missing_explicit_mode():
    runtime = create_runtime(model="fake:model", no_mcp=True)

    with pytest.raises(ValueError, match="fast` or `plan"):
        runtime.invoke({"messages": [HumanMessage(content="hello")]})


async def test_plan_planner_emits_agent_lifecycle_events_and_installs_observability():
    events: list[dict[str, object]] = []
    model = _ToolBindingFakeChatModel(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "write_todos",
                            "args": {"todos": [{"content": "检查日志", "status": "pending"}]},
                            "id": "call-1",
                        }
                    ],
                ),
                AIMessage(content="计划已生成"),
            ]
        )
    )

    async def event_sink(event: dict[str, object]) -> None:
        events.append(event)

    runtime = create_runtime(model=model, no_mcp=True)

    result = await runtime.ainvoke(
        {"mode": "plan", "messages": [HumanMessage(content="hello")]},
        config={"configurable": {"thread_id": "thread-1"}, "metadata": {"fsagent_event_sink": event_sink}},
    )

    assert "__interrupt__" in result
    agent_events = [
        (event["kind"], event["agent_mode"], event["phase"])
        for event in events
        if str(event["kind"]).startswith("agent.")
    ]
    assert ("agent.started", "plan", "planner") in agent_events
    assert ("agent.completed", "plan", "planner") in agent_events


async def test_plan_planner_does_not_receive_execution_tools_before_review():
    @tool
    def external_search(query: str) -> str:
        """Search external systems."""
        return query

    model = _ToolBindingFakeChatModel(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "write_todos",
                            "args": {"todos": [{"content": "检查日志", "status": "pending"}]},
                            "id": "call-1",
                        }
                    ],
                ),
                AIMessage(content="计划已生成"),
            ]
        )
    )
    runtime = create_runtime(model=model, tools=[external_search], no_mcp=True)

    result = await runtime.ainvoke(
        {"mode": "plan", "messages": [HumanMessage(content="hello")]},
        config={"configurable": {"thread_id": "thread-1"}},
    )

    assert "__interrupt__" in result
    assert [getattr(tool_item, "name", None) for tool_item in model.bound_tools] == ["write_todos"]


async def test_plan_planner_describes_executor_capabilities_without_binding_them():
    @tool
    def external_search(query: str) -> str:
        """Search external systems."""
        return query

    model = _ToolBindingFakeChatModel(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "write_todos",
                            "args": {"todos": [{"content": "检查日志", "status": "pending"}]},
                            "id": "call-1",
                        }
                    ],
                ),
                AIMessage(content="计划已生成"),
            ]
        )
    )
    runtime = create_runtime(
        model=model,
        tools=[external_search],
        skills=["diagnostics-skill"],
        memory=["runtime-memory"],
        no_mcp=True,
    )

    result = await runtime.ainvoke(
        {"mode": "plan", "messages": [HumanMessage(content="hello")]},
        config={"configurable": {"thread_id": "thread-1"}},
    )

    assert "__interrupt__" in result
    first_model_call = "\n".join(str(message.content) for message in model.call_messages[0])
    assert "During planning you may only call `write_todos`" in first_model_call
    assert "All proposed todos must use `pending` status" in first_model_call
    assert "mark your first task" not in first_model_call
    assert "in_progress BEFORE beginning" not in first_model_call
    assert "read_file - Read file contents after plan approval." in first_model_call
    assert "external_search - Search external systems." in first_model_call
    assert "diagnostics-skill" in first_model_call
    assert "runtime-memory" in first_model_call
    assert [getattr(tool_item, "name", None) for tool_item in model.bound_tools] == ["write_todos"]


async def test_plan_planner_describes_local_skill_metadata_without_binding_skills(tmp_path: Path):
    skill_source = tmp_path / "skills"
    skill_dir = skill_source / "diagnostics-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: diagnostics-skill
description: Diagnose runtime logs and summarize likely failure points.
---

# Diagnostics Skill
""",
        encoding="utf-8",
    )
    model = _ToolBindingFakeChatModel(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "write_todos",
                            "args": {"todos": [{"content": "检查日志", "status": "pending"}]},
                            "id": "call-1",
                        }
                    ],
                ),
                AIMessage(content="计划已生成"),
            ]
        )
    )
    runtime = create_runtime(model=model, skills=[str(skill_source)], no_mcp=True)

    result = await runtime.ainvoke(
        {"mode": "plan", "messages": [HumanMessage(content="hello")]},
        config={"configurable": {"thread_id": "thread-1"}},
    )

    assert "__interrupt__" in result
    first_model_call = "\n".join(str(message.content) for message in model.call_messages[0])
    assert "diagnostics-skill - Diagnose runtime logs and summarize likely failure points." in first_model_call
    assert [getattr(tool_item, "name", None) for tool_item in model.bound_tools] == ["write_todos"]


async def test_plan_planner_describes_backend_skill_metadata_without_binding_skills():
    backend = _SkillMetadataBackend(
        {
            "/skills/backend-skill/SKILL.md": b"""---
name: backend-skill
description: Summarize files available through the executor backend.
---

# Backend Skill
"""
        }
    )
    model = _ToolBindingFakeChatModel(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "write_todos",
                            "args": {"todos": [{"content": "检查日志", "status": "pending"}]},
                            "id": "call-1",
                        }
                    ],
                ),
                AIMessage(content="计划已生成"),
            ]
        )
    )
    runtime = create_runtime(model=model, skills=["/skills"], backend=backend, no_mcp=True)

    result = await runtime.ainvoke(
        {"mode": "plan", "messages": [HumanMessage(content="hello")]},
        config={"configurable": {"thread_id": "thread-1"}},
    )

    assert "__interrupt__" in result
    first_model_call = "\n".join(str(message.content) for message in model.call_messages[0])
    assert "backend-skill - Summarize files available through the executor backend." in first_model_call
    assert [getattr(tool_item, "name", None) for tool_item in model.bound_tools] == ["write_todos"]


async def test_plan_planner_describes_mcp_servers_without_loading_mcp_tools(
    monkeypatch,
    tmp_path: Path,
):
    async def fail_if_mcp_loads(*_args: object, **_kwargs: object) -> object:
        msg = "Planner must not load MCP tools before plan review."
        raise AssertionError(msg)

    config_path = tmp_path / "mcp.json"
    config_path.write_text(
        """
        {
          "mcpServers": {
            "github-mcp": {
              "type": "streamable_http",
              "url": "http://127.0.0.1:31003/mcp/",
              "description": "GitHub project automation.",
              "tools": [
                {"name": "search_repositories", "description": "Search GitHub repositories."},
                {"name": "get_issue", "description": "Read a GitHub issue."}
              ]
            },
            "disabled-mcp": {"type": "streamable_http", "url": "http://127.0.0.1:31004/mcp/", "disabled": true}
          }
        }
        """,
        encoding="utf-8",
    )
    monkeypatch.setattr("fsagent.runtime.graph.load_runtime_mcp_tools", fail_if_mcp_loads)
    model = _ToolBindingFakeChatModel(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "write_todos",
                            "args": {"todos": [{"content": "检查日志", "status": "pending"}]},
                            "id": "call-1",
                        }
                    ],
                ),
                AIMessage(content="计划已生成"),
            ]
        )
    )
    runtime = create_runtime(model=model, mcp_config_path=str(config_path), no_mcp=False)

    result = await runtime.ainvoke(
        {"mode": "plan", "messages": [HumanMessage(content="hello")]},
        config={"configurable": {"thread_id": "thread-1"}},
    )

    assert "__interrupt__" in result
    first_model_call = "\n".join(str(message.content) for message in model.call_messages[0])
    assert "github-mcp - GitHub project automation." in first_model_call
    assert "search_repositories - Search GitHub repositories." in first_model_call
    assert "get_issue - Read a GitHub issue." in first_model_call
    assert "disabled-mcp" not in first_model_call
    assert [getattr(tool_item, "name", None) for tool_item in model.bound_tools] == ["write_todos"]


async def test_plan_planner_only_exposes_write_todos_in_real_agent_stack():
    events: list[dict[str, object]] = []
    model = _ToolBindingFakeChatModel(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "write_todos",
                            "args": {"todos": [{"content": "检查日志", "status": "pending"}]},
                            "id": "call-1",
                        }
                    ],
                ),
                AIMessage(content="计划已生成"),
            ]
        )
    )
    runtime = create_runtime(model=model, no_mcp=True)

    async def event_sink(event: dict[str, object]) -> None:
        events.append(event)

    result = await runtime.ainvoke(
        {"mode": "plan", "messages": [HumanMessage(content="hello")]},
        config={"configurable": {"thread_id": "thread-1"}, "metadata": {"fsagent_event_sink": event_sink}},
    )

    assert "__interrupt__" in result
    first_model_started = next(event for event in events if event["kind"] == "agent.model.started")
    assert first_model_started["phase"] == "planner"
    assert first_model_started["available_tool_count"] == 1
    planner_tool_names = [
        event["tool_name"] for event in events if event["kind"] == "agent.tool.started" and event["phase"] == "planner"
    ]
    assert planner_tool_names == ["write_todos"]


async def test_plan_retry_allows_planner_to_write_todos_again_in_same_thread():
    events: list[dict[str, object]] = []
    model = _ToolBindingFakeChatModel(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "write_todos",
                            "args": {"todos": [{"content": "第一版计划", "status": "pending"}]},
                            "id": "call-1",
                        }
                    ],
                ),
                AIMessage(content="第一版计划已生成"),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "write_todos",
                            "args": {"todos": [{"content": "第二版计划", "status": "pending"}]},
                            "id": "call-2",
                        }
                    ],
                ),
                AIMessage(content="第二版计划已生成"),
            ]
        )
    )
    runtime = create_runtime(model=model, no_mcp=True, checkpointer=InMemorySaver())

    async def event_sink(event: dict[str, object]) -> None:
        events.append(event)

    config = {"configurable": {"thread_id": "retry-thread-1"}, "metadata": {"fsagent_event_sink": event_sink}}
    first_result = await runtime.ainvoke(
        {"mode": "plan", "messages": [HumanMessage(content="hello")]},
        config=config,
    )
    retry_result = await runtime.ainvoke(
        Command(resume={"action": "retry", "feedback": "重新生成计划"}),
        config=config,
    )

    assert "__interrupt__" in first_result
    assert "__interrupt__" in retry_result
    planner_tool_events = [
        event for event in events if event["kind"] == "agent.tool.started" and event["phase"] == "planner"
    ]
    assert [event["tool_name"] for event in planner_tool_events] == ["write_todos", "write_todos"]
    assert [event for event in events if event["kind"] == "agent.tool.failed" and event["phase"] == "planner"] == []


async def test_plan_planner_does_not_load_mcp_tools_before_review(monkeypatch):
    async def fail_if_mcp_loads(*_args: object, **_kwargs: object) -> object:
        msg = "Planner must not load MCP tools before plan review."
        raise AssertionError(msg)

    monkeypatch.setattr("fsagent.runtime.graph.load_runtime_mcp_tools", fail_if_mcp_loads)
    model = _ToolBindingFakeChatModel(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "write_todos",
                            "args": {"todos": [{"content": "检查日志", "status": "pending"}]},
                            "id": "call-1",
                        }
                    ],
                ),
                AIMessage(content="计划已生成"),
            ]
        )
    )
    runtime = create_runtime(model=model, no_mcp=False)

    result = await runtime.ainvoke(
        {"mode": "plan", "messages": [HumanMessage(content="hello")]},
        config={"configurable": {"thread_id": "thread-1"}},
    )

    assert "__interrupt__" in result


async def test_fast_mode_mcp_config_does_not_interrupt_before_tool_call(monkeypatch, tmp_path: Path):
    config_path = tmp_path / "mcp.json"
    config_path.write_text(
        json.dumps({"mcpServers": {"trends-hub": {"command": "npx", "args": ["trends-hub"]}}}),
        encoding="utf-8",
    )

    async def fake_load_runtime_mcp_tools(*_args: object, **_kwargs: object) -> RuntimeMCPResult:
        return RuntimeMCPResult(tools=[])

    monkeypatch.setattr("fsagent.runtime.graph.load_runtime_mcp_tools", fake_load_runtime_mcp_tools)
    runtime = create_runtime(
        model=_ToolBindingFakeChatModel(messages=iter([AIMessage(content="done")])),
        mcp_config_path=str(config_path),
        trust_project_mcp=True,
        checkpointer=InMemorySaver(),
    )

    result = await runtime.ainvoke(
        {"mode": "fast", "messages": [HumanMessage(content="hello")]},
        config={"configurable": {"thread_id": "fast-mcp-no-server-review-thread"}},
    )

    assert "__interrupt__" not in result
    assert result["final_response"] == "done"


async def test_fast_mode_high_risk_mcp_tool_uses_tool_review(monkeypatch, tmp_path: Path):
    @tool
    def filesystem_delete_file(path: str) -> str:
        """Delete a local file."""
        return f"deleted {path}"

    filesystem_delete_file.metadata = {"fsagent_tool_source": "mcp", "fsagent_tool_risk": "high"}

    config_path = tmp_path / "mcp.json"
    config_path.write_text(
        json.dumps({"mcpServers": {"local-files": {"command": "python", "args": ["server.py"]}}}),
        encoding="utf-8",
    )

    async def fake_load_runtime_mcp_tools(*_args: object, **_kwargs: object) -> RuntimeMCPResult:
        return RuntimeMCPResult(tools=[filesystem_delete_file])

    monkeypatch.setattr("fsagent.runtime.graph.load_runtime_mcp_tools", fake_load_runtime_mcp_tools)
    model = _ToolBindingFakeChatModel(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "filesystem_delete_file",
                            "args": {"path": "out.txt"},
                            "id": "call-delete",
                        }
                    ],
                ),
                AIMessage(content="done"),
            ]
        )
    )
    runtime = create_runtime(
        model=model,
        mcp_config_path=str(config_path),
        trust_project_mcp=True,
        checkpointer=InMemorySaver(),
    )
    result = await runtime.ainvoke(
        {"mode": "fast", "messages": [HumanMessage(content="delete file")]},
        config={"configurable": {"thread_id": "fast-mcp-tool-review-thread"}},
    )

    assert "__interrupt__" in result
    interrupt = result["__interrupt__"][0].value
    assert interrupt["action_requests"][0]["name"] == "filesystem_delete_file"
    assert interrupt["review_configs"][0]["allowed_decisions"] == ["approve", "edit", "reject"]


async def test_plan_mode_deviation_review_triggers_only_from_state(monkeypatch):
    async def fake_execute_plan(**kwargs: object) -> object:
        return SimpleNamespace(
            todos=kwargs["todos"],
            execution_log=[],
            evidence=[],
            final_result="executor requested deviation in natural language",
            deviation_requested=False,
            deviation_reason=None,
        )

    async def fake_verify_execution_async(**_kwargs: object) -> object:
        return SimpleNamespace(verification=[], status="completed")

    monkeypatch.setattr("fsagent.runtime.graph.create_deep_agent", lambda **_kwargs: object())
    monkeypatch.setattr("fsagent.runtime.graph.execute_plan", fake_execute_plan)
    monkeypatch.setattr("fsagent.runtime.graph.verify_execution_async", fake_verify_execution_async)
    model = _ToolBindingFakeChatModel(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "write_todos",
                            "args": {"todos": [{"content": "检查日志", "status": "pending"}]},
                            "id": "call-1",
                        }
                    ],
                ),
                AIMessage(content="计划已生成"),
            ]
        )
    )
    runtime = create_runtime(model=model, no_mcp=True, checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "deviation-natural-language-thread"}}

    first_result = await runtime.ainvoke({"mode": "plan", "messages": [HumanMessage(content="hello")]}, config=config)
    final_result = await runtime.ainvoke(Command(resume={"action": "approve"}), config=config)

    assert first_result["__interrupt__"][0].value["kind"] == "plan_review"
    assert "__interrupt__" not in final_result
    assert final_result["status"] == "completed"
    assert "executor requested deviation in natural language" in final_result["final_response"]


async def test_plan_mode_formatter_omits_unverified_executor_summary_when_revision_is_needed(monkeypatch):
    async def fake_execute_plan(**_kwargs: object) -> object:
        return SimpleNamespace(
            todos=[
                {"content": "测试 GitHub MCP 工具", "status": "failed"},
                {"content": "汇总所有 MCP 工具的测试结果和可用性状态", "status": "completed"},
            ],
            execution_log=[
                {"content": "测试 GitHub MCP 工具", "status": "failed", "error": "Tool execution requires approval"},
                {
                    "content": "汇总所有 MCP 工具的测试结果和可用性状态",
                    "status": "completed",
                    "result": "## MCP 工具测试结果和可用性状态汇总\n\nGitHub MCP 工具：✅ 可用",
                },
            ],
            evidence=[],
            final_result="## MCP 工具测试结果和可用性状态汇总\n\nGitHub MCP 工具：✅ 可用",
            deviation_requested=False,
            deviation_reason=None,
        )

    async def fake_verify_execution_async(**_kwargs: object) -> object:
        return SimpleNamespace(
            verification=[
                {
                    "id": "verification-001",
                    "status": "skipped",
                    "reason": "No verification command was provided.",
                }
            ],
            status="needs_revision",
        )

    monkeypatch.setattr("fsagent.runtime.graph.create_deep_agent", lambda **_kwargs: object())
    monkeypatch.setattr("fsagent.runtime.graph.execute_plan", fake_execute_plan)
    monkeypatch.setattr("fsagent.runtime.graph.verify_execution_async", fake_verify_execution_async)
    model = _ToolBindingFakeChatModel(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "write_todos",
                            "args": {"todos": [{"content": "测试 GitHub MCP 工具", "status": "pending"}]},
                            "id": "call-1",
                        }
                    ],
                ),
                AIMessage(content="计划已生成"),
            ]
        )
    )
    runtime = create_runtime(model=model, no_mcp=True, checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "formatter-needs-revision-thread"}}

    first_result = await runtime.ainvoke({"mode": "plan", "messages": [HumanMessage(content="hello")]}, config=config)
    final_result = await runtime.ainvoke(Command(resume={"action": "approve"}), config=config)

    assert "__interrupt__" in first_result
    assert final_result["status"] == "needs_revision"
    assert "Execution requires revision." in final_result["final_response"]
    assert "GitHub MCP 工具：✅ 可用" not in final_result["final_response"]


@pytest.mark.parametrize(
    ("action", "expected_status"),
    [
        ("approve", "completed"),
        ("replan", "needs_revision"),
        ("cancel", "cancelled"),
    ],
)
async def test_plan_mode_deviation_review_resume_actions_update_state(
    monkeypatch,
    action: str,
    expected_status: str,
):
    async def fake_execute_plan(**kwargs: object) -> object:
        return SimpleNamespace(
            todos=kwargs["todos"],
            execution_log=[],
            evidence=[],
            final_result="executor done",
            deviation_requested=True,
            deviation_reason="Need to inspect logs before editing config.",
        )

    async def fake_verify_execution_async(**_kwargs: object) -> object:
        return SimpleNamespace(verification=[], status="completed")

    monkeypatch.setattr("fsagent.runtime.graph.create_deep_agent", lambda **_kwargs: object())
    monkeypatch.setattr("fsagent.runtime.graph.execute_plan", fake_execute_plan)
    monkeypatch.setattr("fsagent.runtime.graph.verify_execution_async", fake_verify_execution_async)
    model = _ToolBindingFakeChatModel(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "write_todos",
                            "args": {"todos": [{"content": "检查日志", "status": "pending"}]},
                            "id": "call-1",
                        }
                    ],
                ),
                AIMessage(content="计划已生成"),
            ]
        )
    )
    runtime = create_runtime(model=model, no_mcp=True, checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": f"deviation-review-{action}-thread"}}

    first_result = await runtime.ainvoke(
        {
            "mode": "plan",
            "messages": [HumanMessage(content="hello")],
        },
        config=config,
    )
    second_result = await runtime.ainvoke(Command(resume={"action": "approve"}), config=config)
    final_result = await runtime.ainvoke(Command(resume={"action": action}), config=config)

    assert first_result["__interrupt__"][0].value["kind"] == "plan_review"
    assert second_result["__interrupt__"][0].value["kind"] == "deviation_review"
    assert "Need to inspect logs" in second_result["__interrupt__"][0].value["proposed_input_summary"]
    assert final_result["status"] == expected_status
    if action == "approve":
        assert final_result["deviation_requested"] is False


async def test_fast_mode_high_risk_tool_uses_hitl_interrupt():
    @tool
    def execute(command: str) -> str:
        """Run a local command."""
        return f"ran {command}"

    model = _ToolBindingFakeChatModel(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "execute",
                            "args": {"command": "pytest"},
                            "id": "call-execute",
                        }
                    ],
                ),
                AIMessage(content="done"),
            ]
        )
    )
    runtime = create_runtime(model=model, tools=[execute], no_mcp=True, checkpointer=InMemorySaver())

    result = await runtime.ainvoke(
        {"mode": "fast", "messages": [HumanMessage(content="run tests")]},
        config={"configurable": {"thread_id": "fast-hitl-thread"}},
    )

    assert "__interrupt__" in result
    interrupt = result["__interrupt__"][0].value
    assert interrupt["action_requests"][0]["name"] == "execute"
    assert interrupt["review_configs"][0]["allowed_decisions"] == ["approve", "edit", "reject"]


async def test_fast_mode_write_tool_uses_hitl_interrupt():
    @tool
    def write_file(path: str, content: str) -> str:
        """Write a local file."""
        return f"wrote {path}: {content}"

    model = _ToolBindingFakeChatModel(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "write_file",
                            "args": {"path": "out.txt", "content": "ok"},
                            "id": "call-write",
                        }
                    ],
                ),
                AIMessage(content="done"),
            ]
        )
    )
    runtime = create_runtime(model=model, tools=[write_file], no_mcp=True, checkpointer=InMemorySaver())

    result = await runtime.ainvoke(
        {"mode": "fast", "messages": [HumanMessage(content="write a file")]},
        config={"configurable": {"thread_id": "fast-write-hitl-thread"}},
    )

    assert "__interrupt__" in result
    interrupt = result["__interrupt__"][0].value
    assert interrupt["action_requests"][0]["name"] == "write_file"
    assert interrupt["review_configs"][0]["allowed_decisions"] == ["approve", "edit", "reject"]


async def test_fast_mode_suppresses_tool_calls_after_tool_round_is_used():
    @tool
    def read_file(path: str) -> str:
        """Read a file."""
        return f"contents of {path}"

    model = _ToolBindingFakeChatModel(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "read_file",
                            "args": {"path": "README.md"},
                            "id": "call-read",
                        }
                    ],
                ),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "unknown",
                            "args": {},
                            "id": "call-late",
                        }
                    ],
                ),
            ]
        )
    )
    runtime = create_runtime(model=model, tools=[read_file], no_mcp=True)

    result = await runtime.ainvoke(
        {"mode": "fast", "messages": [HumanMessage(content="read then answer")]},
        config={"configurable": {"thread_id": "fast-post-round-tool-call-thread"}},
    )

    assert "Unknown tools are denied by default" not in result["final_response"]
    assert "Fast 模式已使用过一次工具调用" in result["final_response"]


async def test_plan_executor_delegates_hitl_installation_to_deep_agent(monkeypatch):
    @tool
    def execute(command: str) -> str:
        """Run a local command."""
        return f"ran {command}"

    captured: dict[str, Any] = {}

    class SentinelHumanInTheLoopMiddleware:
        def __init__(self, interrupt_on: dict[str, object]) -> None:
            self.interrupt_on = interrupt_on

    def fake_create_deep_agent(**kwargs: object) -> object:
        captured.update(kwargs)
        return object()

    async def fake_execute_plan(**kwargs: object) -> object:
        return SimpleNamespace(
            todos=kwargs["todos"],
            execution_log=[],
            evidence=[],
            final_result="executor done",
            deviation_requested=False,
            deviation_reason=None,
        )

    monkeypatch.setattr("fsagent.runtime.graph.HumanInTheLoopMiddleware", SentinelHumanInTheLoopMiddleware)
    monkeypatch.setattr("fsagent.runtime.graph.create_deep_agent", fake_create_deep_agent)
    monkeypatch.setattr("fsagent.runtime.graph.execute_plan", fake_execute_plan)
    model = _ToolBindingFakeChatModel(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "write_todos",
                            "args": {"todos": [{"content": "检查日志", "status": "pending"}]},
                            "id": "call-1",
                        }
                    ],
                ),
                AIMessage(content="计划已生成"),
            ]
        )
    )
    runtime = create_runtime(model=model, tools=[execute], no_mcp=True, checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "executor-hitl-installation-thread"}}

    first_result = await runtime.ainvoke({"mode": "plan", "messages": [HumanMessage(content="hello")]}, config=config)
    final_result = await runtime.ainvoke(Command(resume={"action": "approve"}), config=config)

    assert "__interrupt__" in first_result
    assert "executor done" in final_result["final_response"]
    assert captured["interrupt_on"] == {"execute": {"allowed_decisions": ["approve", "edit", "reject"]}}
    assert all(not isinstance(middleware, SentinelHumanInTheLoopMiddleware) for middleware in captured["middleware"])


async def test_plan_executor_does_not_expose_write_todos_to_model():
    model = _ToolBindingFakeChatModel(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "write_todos",
                            "args": {"todos": [{"content": "检查日志", "status": "pending"}]},
                            "id": "call-1",
                        }
                    ],
                ),
                AIMessage(content="计划已生成"),
                AIMessage(content="检查完成"),
            ]
        )
    )
    runtime = create_runtime(model=model, no_mcp=True, checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "executor-no-write-todos-thread"}}

    first_result = await runtime.ainvoke({"mode": "plan", "messages": [HumanMessage(content="hello")]}, config=config)
    final_result = await runtime.ainvoke(Command(resume={"action": "approve"}), config=config)

    assert "__interrupt__" in first_result
    assert "检查完成" in final_result["final_response"]
    assert "write_todos" not in [getattr(tool_item, "name", None) for tool_item in model.bound_tools]
    executor_model_call = "\n".join(str(message.content) for message in model.call_messages[-1])
    assert "## `write_todos`" not in executor_model_call
    assert "You have access to the `write_todos` tool" not in executor_model_call


async def test_plan_executor_ignores_unexpected_write_todos_tool_call():
    model = _ToolBindingFakeChatModel(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "write_todos",
                            "args": {"todos": [{"content": "执行项", "status": "pending"}]},
                            "id": "planner-call",
                        }
                    ],
                ),
                AIMessage(content="计划已生成"),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "write_todos",
                            "args": {"todos": [{"content": "执行项", "status": "completed"}]},
                            "id": "executor-call",
                        }
                    ],
                ),
                AIMessage(content="执行完成"),
            ]
        )
    )
    runtime = create_runtime(model=model, no_mcp=True, checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "executor-unexpected-write-todos-thread"}}

    first_result = await runtime.ainvoke({"mode": "plan", "messages": [HumanMessage(content="hello")]}, config=config)
    final_result = await runtime.ainvoke(Command(resume={"action": "approve"}), config=config)

    assert "__interrupt__" in first_result
    assert "Unknown tools are denied by default" not in final_result["final_response"]
    assert final_result["todos"][0]["status"] == "completed"
    assert final_result["execution_log"][0]["result"] == "执行完成"


async def test_planner_repo_rules_helper_extracts_rules_from_system_prompt(monkeypatch):
    """Test that _planner_repo_rules extracts repo rules from system prompt."""
    # Test 1: Pure rules text - should return as-is
    pure_rules = "默认使用中文回答。遵循项目代码规范。"
    assert _planner_repo_rules(pure_rules) == pure_rules

    # Test 2: AGENTS.md format - should extract only rules sections
    agents_md_content = """# AGENTS.md

本文件适用于项目目录及其子目录。

## 语言与沟通

- 默认使用中文回答问题。
- 说明变更时保持简洁。

## 项目定位

- 本项目是 Deep Agents 的扩展。

## Python 开发

- 需要 Python >=3.11。
- 使用 uv 管理依赖。
"""
    extracted_rules = _planner_repo_rules(agents_md_content)
    assert extracted_rules is not None
    assert "默认使用中文回答问题" in extracted_rules
    assert "需要 Python >=3.11" in extracted_rules
    assert "# AGENTS.md" not in extracted_rules
    assert "本文件适用于项目目录" not in extracted_rules

    # Test 3: Use monkeypatch to verify the helper is called during planner invocation
    captured_repo_rules = []

    def capture_repo_rules(system_prompt: str | SystemMessage | None) -> str | None:
        result = _planner_repo_rules(system_prompt)
        captured_repo_rules.append(result)
        return result

    monkeypatch.setattr("fsagent.runtime.graph._planner_repo_rules", capture_repo_rules)

    model = _ToolBindingFakeChatModel(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "write_todos",
                            "args": {"todos": [{"content": "分析输入", "status": "pending"}]},
                            "id": "call-1",
                        }
                    ],
                ),
                AIMessage(content="计划已生成"),
            ]
        )
    )
    repo_rules = "默认使用中文回答。遵循项目代码规范。"
    runtime = create_runtime(model=model, system_prompt=repo_rules, no_mcp=True)

    result = await runtime.ainvoke(
        {"mode": "plan", "messages": [HumanMessage(content="分析项目")]},
        config={"configurable": {"thread_id": "thread-1"}},
    )

    assert "__interrupt__" in result
    assert len(captured_repo_rules) == 1
    assert captured_repo_rules[0] == repo_rules
    first_model_call = "\n".join(str(message.content) for message in model.call_messages[0])
    assert "[trusted:repo_rules]" in first_model_call
    assert "默认使用中文回答" in first_model_call


class _ToolBindingFakeChatModel(BaseChatModel):
    messages: Iterator[AIMessage | str] = Field(exclude=True)
    bound_tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool] = ()
    call_messages: list[list[BaseMessage]] = Field(default_factory=list)

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: object,
    ) -> Runnable[LanguageModelInput, AIMessage]:
        _ = (tool_choice, kwargs)
        self.bound_tools = tools
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: object,
    ) -> ChatResult:
        _ = (messages, stop, run_manager, kwargs)
        self.call_messages.append(messages)
        message = next(self.messages)
        ai_message = AIMessage(content=message) if isinstance(message, str) else message
        return ChatResult(generations=[ChatGeneration(message=ai_message)])

    @property
    def _llm_type(self) -> str:
        return "fsagent-test-tool-binding-fake-chat-model"


class _SkillMetadataBackend(BackendProtocol):
    def __init__(self, files: dict[str, bytes]) -> None:
        self._files = files

    def ls(self, path: str) -> LsResult:
        prefix = path.rstrip("/") + "/"
        entries = []
        seen_dirs: set[str] = set()
        for file_path in self._files:
            if not file_path.startswith(prefix):
                continue
            relative = file_path.removeprefix(prefix)
            skill_dir = relative.split("/", 1)[0]
            dir_path = f"{prefix}{skill_dir}"
            if dir_path not in seen_dirs:
                entries.append({"path": dir_path, "is_dir": True})
                seen_dirs.add(dir_path)
        return LsResult(entries=entries)

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return [
            FileDownloadResponse(
                path=path, content=self._files.get(path), error=None if path in self._files else "file_not_found"
            )
            for path in paths
        ]
