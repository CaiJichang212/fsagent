import pytest
from langchain_core.messages import HumanMessage

from fsagent.runtime.graph import create_runtime


def test_create_runtime_compiles_explicit_mode_graph():
    runtime = create_runtime(model="fake:model", no_mcp=True)

    assert runtime is not None


def test_runtime_rejects_missing_explicit_mode():
    runtime = create_runtime(model="fake:model", no_mcp=True)

    with pytest.raises(ValueError, match="fast` or `plan"):
        runtime.invoke({"messages": [HumanMessage(content="hello")]})
