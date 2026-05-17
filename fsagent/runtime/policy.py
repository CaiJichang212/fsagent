"""Runtime tool policy profiles and middleware."""

from __future__ import annotations

import inspect
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from langchain.agents.middleware.types import AgentMiddleware, ToolCallRequest

from fsagent.runtime.risk import is_high_risk_tool_text
from fsagent.runtime.state import RuntimeState
from fsagent.runtime.tools import ToolMetadata, tool_metadata

if TYPE_CHECKING:
    from langchain_core.messages import ToolMessage
    from langchain_core.tools import BaseTool

ToolPolicyProfile = Literal["dev-default", "locked-down", "ci-eval"]
ToolPolicyAction = Literal["allow", "review", "deny"]
ToolRisk = Literal["low", "medium", "high", "critical"]
ProgressCallback = Callable[[Mapping[str, object]], object | Awaitable[object]]

_READ_TOOLS = {"ls", "glob", "grep", "read_file"}
_WRITE_TOOLS = {"write_file", "edit_file"}
_EXECUTE_TOOLS = {"execute", "bash", "shell"}
_TASK_TOOLS = {"task"}
_REVIEW_DECISIONS = {"allowed_decisions": ["approve", "edit", "reject"]}


@dataclass(frozen=True, slots=True)
class ToolPolicyDecision:
    """Decision returned by a policy profile for one tool call."""

    tool_name: str
    risk: ToolRisk
    action: ToolPolicyAction
    reason: str
    profile: str


def evaluate_tool_policy(
    tool_name: str,
    *,
    profile: str = "dev-default",
    phase: str = "executor",
    metadata: ToolMetadata | None = None,
) -> ToolPolicyDecision:
    """Evaluate the configured tool policy for a tool name."""
    normalized_profile = _profile(profile)
    metadata_risk = _risk(getattr(metadata, "risk", None))
    source = getattr(metadata, "source", "runtime")
    if normalized_profile == "ci-eval":
        return _ci_eval_decision(tool_name, profile=normalized_profile)
    if normalized_profile == "locked-down":
        return _locked_down_decision(
            tool_name,
            source=source,
            metadata_risk=metadata_risk,
            profile=normalized_profile,
        )
    return _dev_default_decision(
        tool_name,
        phase=phase,
        source=source,
        metadata_risk=metadata_risk,
        profile=normalized_profile,
    )


def policy_interrupt_on_for_tools(
    tools: Sequence[BaseTool | Callable[..., Any] | dict[str, Any]],
    *,
    profile: str = "dev-default",
    phase: str = "executor",
    base: Mapping[str, bool | dict[str, object]] | None = None,
) -> dict[str, bool | dict[str, object]]:
    """Build a LangChain HITL `interrupt_on` config from actual tool names."""
    interrupt_on: dict[str, bool | dict[str, object]] = dict(base or {})
    for tool in tools:
        metadata = tool_metadata(tool)
        decision = evaluate_tool_policy(metadata.name, profile=profile, phase=phase, metadata=metadata)
        if decision.action == "review":
            interrupt_on[metadata.name] = _strengthened_review_config(interrupt_on.get(metadata.name))
    return interrupt_on


class ToolPolicyMiddleware(AgentMiddleware[RuntimeState, Any, Any]):
    """Emit policy decisions and block denied tools before execution."""

    state_schema = RuntimeState

    def __init__(
        self,
        *,
        profile: str = "dev-default",
        phase: str = "executor",
        on_event: ProgressCallback | None = None,
    ) -> None:
        """Initialize middleware with a policy profile."""
        self._profile = _profile(profile)
        self._phase = phase
        self._on_event = on_event

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage]],
    ) -> ToolMessage:
        """Evaluate policy around an async tool call."""
        metadata = tool_metadata(request.tool)
        tool_call_id = str(request.tool_call.get("id") or "")
        decision = evaluate_tool_policy(metadata.name, profile=self._profile, phase=self._phase, metadata=metadata)
        await _emit_async(self._on_event, _policy_event(decision, request.tool_call.get("args"), tool_call_id))
        if decision.action == "deny":
            raise PermissionError(decision.reason)
        return await handler(request)


def _dev_default_decision(  # noqa: PLR0911
    tool_name: str,
    *,
    phase: str,
    source: str,
    metadata_risk: ToolRisk | None,
    profile: ToolPolicyProfile,
) -> ToolPolicyDecision:
    if metadata_risk in {"high", "critical"}:
        return _decision(tool_name, metadata_risk, "review", "Tool metadata marks this call as high risk.", profile)
    if source == "mcp":
        return _decision(
            tool_name,
            metadata_risk or "medium",
            "allow",
            "MCP tool allowed after server enablement.",
            profile,
        )
    if tool_name in _READ_TOOLS:
        return _decision(tool_name, "low", "allow", "Read-only filesystem tool.", profile)
    if tool_name in _WRITE_TOOLS:
        return _decision(tool_name, "high", "review", "Write tools require tool review.", profile)
    if tool_name in _EXECUTE_TOOLS:
        return _decision(tool_name, "high", "review", "Local execution requires tool review.", profile)
    if tool_name in _TASK_TOOLS:
        action = "allow" if phase == "executor" else "deny"
        return _decision(
            tool_name, "medium", action, "Subtask delegation is allowed only during approved execution.", profile
        )
    return _decision(tool_name, "high", "deny", "Unknown tools are denied by default.", profile)


def _locked_down_decision(
    tool_name: str,
    *,
    source: str,
    metadata_risk: ToolRisk | None,
    profile: ToolPolicyProfile,
) -> ToolPolicyDecision:
    if tool_name in _READ_TOOLS:
        return _decision(
            tool_name,
            metadata_risk or "low",
            "allow",
            "Read-only tool allowed by locked-down policy.",
            profile,
        )
    if source == "mcp":
        if metadata_risk in {"high", "critical"}:
            return _decision(
                tool_name,
                metadata_risk,
                "review",
                "MCP tool metadata marks this call as high risk.",
                profile,
            )
        return _decision(
            tool_name,
            metadata_risk or "low",
            "allow",
            "Read-only MCP tool allowed by locked-down policy.",
            profile,
        )
    if tool_name in _WRITE_TOOLS | _EXECUTE_TOOLS | _TASK_TOOLS:
        return _decision(
            tool_name,
            metadata_risk or "high",
            "review",
            "Tool requires review in locked-down policy.",
            profile,
        )
    return _decision(tool_name, "high", "deny", "Unknown tools are denied by locked-down policy.", profile)


def _ci_eval_decision(tool_name: str, *, profile: ToolPolicyProfile) -> ToolPolicyDecision:
    if tool_name in _READ_TOOLS:
        return _decision(tool_name, "low", "allow", "Read-only tools are allowed in CI eval.", profile)
    return _decision(tool_name, "high", "deny", "CI eval profile denies side-effecting tools.", profile)


def _decision(
    tool_name: str,
    risk: ToolRisk,
    action: ToolPolicyAction,
    reason: str,
    profile: ToolPolicyProfile,
) -> ToolPolicyDecision:
    return ToolPolicyDecision(
        tool_name=tool_name,
        risk=risk,
        action=action,
        reason=reason,
        profile=profile,
    )


def _policy_event(decision: ToolPolicyDecision, args: object, tool_call_id: str) -> dict[str, object]:
    status = {"allow": "allowed", "review": "review_required", "deny": "denied"}[decision.action]
    return {
        "kind": "tool.policy_decision",
        "message": f"Tool policy {decision.action}: {decision.tool_name}",
        "tool_name": decision.tool_name,
        "tool_call_id": tool_call_id,
        "risk": decision.risk,
        "profile": decision.profile,
        "policyDecision": decision.action,
        "reason": decision.reason,
        "tool_calls": [
            {
                "id": tool_call_id or f"tool-{decision.tool_name}",
                "name": decision.tool_name,
                "risk": decision.risk,
                "status": status,
                "inputSummary": _input_summary(args),
            }
        ],
    }


def high_risk_tool_name_or_description(name: object, description: object = None) -> bool:
    """Return whether tool display text suggests high-risk side effects."""
    return is_high_risk_tool_text(name, description)


def _strengthened_review_config(current: object) -> bool | dict[str, object]:
    if current is None or current is False:
        return dict(_REVIEW_DECISIONS)
    if isinstance(current, dict):
        return current
    if current is True:
        return True
    return dict(_REVIEW_DECISIONS)


async def _emit_async(callback: ProgressCallback | None, event: Mapping[str, object]) -> None:
    if callback is None:
        return
    result = callback(event)
    if inspect.isawaitable(result):
        await result


def _input_summary(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, default=str)[:500]
    except TypeError:
        return str(value)[:500]


def _profile(value: str) -> ToolPolicyProfile:
    if value in {"dev-default", "locked-down", "ci-eval"}:
        return value
    return "dev-default"


def _risk(value: object) -> ToolRisk | None:
    if value in {"low", "medium", "high", "critical"}:
        return value
    return None
