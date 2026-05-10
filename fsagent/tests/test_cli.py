from types import SimpleNamespace

from fsagent.cli import format_cli_output, format_runtime_error


def test_format_runtime_error_explains_null_choices_response():
    error = TypeError("Received response with null value for 'choices'.")

    message = format_runtime_error(error)

    assert message is not None
    assert "choices" in message
    assert "MODEL" in message
    assert "BASE_URL" in message


def test_format_cli_output_renders_plan_review_interrupt():
    output = format_cli_output(
        {
            "__interrupt__": (
                SimpleNamespace(
                    value={
                        "kind": "plan_review",
                        "plan_meta": {"goal": "Test MCP tools"},
                        "todos": [{"content": "List tools", "status": "pending"}],
                    }
                ),
            )
        }
    )

    assert output is not None
    assert "Plan review required" in output
    assert "Test MCP tools" in output
    assert "List tools" in output
