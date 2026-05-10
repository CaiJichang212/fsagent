import pytest

from fsagent.slash_router import parse_slash_mode


def test_parse_fast_request_strips_prefix_and_whitespace():
    routed = parse_slash_mode("  /fast   当前仓库怎么运行单元测试？  ")

    assert routed.mode == "fast"
    assert routed.content == "当前仓库怎么运行单元测试？"


def test_parse_plan_request_strips_prefix_and_whitespace():
    routed = parse_slash_mode("/plan 给这个仓库增加 MCP 工具集成并补测试")

    assert routed.mode == "plan"
    assert routed.content == "给这个仓库增加 MCP 工具集成并补测试"


@pytest.mark.parametrize("text", ["hello", "/fast", "/plan", "/slow task", ""])
def test_parse_slash_mode_requires_explicit_supported_mode(text):
    with pytest.raises(ValueError, match="/fast or /plan"):
        parse_slash_mode(text)
