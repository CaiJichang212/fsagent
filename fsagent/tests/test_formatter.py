from fsagent.runtime.formatter import format_final_report


def test_format_final_report_uses_required_template_sections():
    report = format_final_report(
        result="完成了架构分析。",
        todos=[
            {"content": "分析目录结构", "status": "completed"},
            {"content": "识别核心模块", "status": "pending"},
        ],
        execution_log=[
            {"content": "分析目录结构", "status": "completed", "result": "发现 libs/ 为核心包目录。"},
            {"content": "识别核心模块", "status": "failed", "error": "模型调用失败"},
        ],
        artifacts=["pytest fsagent/tests -q"],
    )

    assert report.startswith("## Result\n完成了架构分析。")
    assert "## Execution Summary" in report
    assert "- 发现 libs/ 为核心包目录。" in report
    assert "## Plan Status" in report
    assert "- [x] 分析目录结构" in report
    assert "- [!] 识别核心模块 - 模型调用失败" in report
    assert "## Artifacts And Evidence" in report
    assert "- pytest fsagent/tests -q" in report


def test_format_final_report_separates_executed_and_skipped_entries():
    report = format_final_report(
        result="完成。",
        todos=[
            {"content": "真实执行事项", "status": "completed"},
            {"content": "预先完成事项", "status": "completed"},
        ],
        execution_log=[
            {"content": "真实执行事项", "status": "completed", "result": "真实执行结果"},
            {"content": "预先完成事项", "status": "skipped", "result": "Already completed."},
        ],
    )

    assert "### Executed" in report
    assert "- 真实执行结果" in report
    assert "### Skipped / Already Completed" in report
    assert "- 预先完成事项 - Already completed." in report
    assert "Skipped / already completed: 预先完成事项" in report


def test_format_final_report_lists_verification_records_or_skipped_reason():
    report = format_final_report(
        result="完成。",
        todos=[{"id": "todo-001", "content": "运行测试", "status": "completed"}],
        execution_log=[{"id": "log-001", "todo_id": "todo-001", "content": "运行测试", "status": "completed"}],
        verification=[
            {
                "id": "verification-001",
                "todo_id": "todo-001",
                "command": None,
                "status": "skipped",
                "reason": "No verification command was provided.",
            }
        ],
    )

    assert "## Verification" in report
    assert "- verification-001: skipped - No verification command was provided." in report
