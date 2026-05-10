import pytest

from fsagent.runtime.approval import build_plan_review_payload, normalize_plan, normalize_review_command


def test_normalize_plan_defaults_status_and_removes_extra_fields():
    todos = normalize_plan(
        [
            {"content": "分析目录结构", "ignored": "value"},
            {"content": "识别核心模块", "status": "completed", "other": "value"},
        ]
    )

    assert todos == [
        {"content": "分析目录结构", "status": "pending"},
        {"content": "识别核心模块", "status": "completed"},
    ]


def test_normalize_plan_rejects_empty_todos():
    with pytest.raises(ValueError, match="at least one"):
        normalize_plan([])


def test_normalize_plan_rejects_invalid_status():
    with pytest.raises(ValueError, match="Invalid todo status"):
        normalize_plan([{"content": "分析目录结构", "status": "blocked"}])


def test_normalize_review_command_accepts_retry_without_feedback():
    command = normalize_review_command({"action": "retry"})

    assert command == {"action": "retry"}


def test_normalize_review_command_requires_todos_for_edit():
    with pytest.raises(ValueError, match="edit requires todos"):
        normalize_review_command({"action": "edit"})


def test_build_plan_review_payload_uses_todos_as_plan():
    payload = build_plan_review_payload(
        todos=[{"content": "分析目录结构", "status": "pending"}],
        plan_meta={"goal": "分析项目", "final_output_format": "markdown"},
    )

    assert payload["kind"] == "plan_review"
    assert payload["todos"] == [{"content": "分析目录结构", "status": "pending"}]
    assert payload["plan_meta"]["goal"] == "分析项目"
    assert payload["allowed_actions"] == ["approve", "edit", "retry", "cancel"]
