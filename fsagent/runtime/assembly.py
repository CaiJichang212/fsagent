"""Runtime profile assembly for fsagent."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, cast

from deepagents import FilesystemPermission

RuntimeProfile = Literal["dev-default", "locked-down", "ci-eval"]
BackendProfile = Literal["ephemeral", "workspace-readonly", "workspace-edit", "sandbox-exec", "store-backed"]
PermissionProfile = Literal["workspace-readonly", "workspace-edit", "locked-down", "ci-eval"]
ToolPolicyProfile = Literal["dev-default", "locked-down", "ci-eval"]
_RUNTIME_PROFILE_KIND = "runtime"
_BACKEND_PROFILE_KIND = "backend"
_PERMISSION_PROFILE_KIND = "permission"
_TOOL_POLICY_PROFILE_KIND = "tool_policy"


class UnsupportedRuntimeAssemblyProfileError(ValueError):
    """Raised when an API runtime assembly profile name is unsupported."""

    def __init__(self, profile_kind: str, value: str) -> None:
        """Initialize the error with the unsupported profile kind and value."""
        message = f"Unsupported {profile_kind.replace('_', ' ')} profile: {value}"
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class RuntimeAssemblyConfig:
    """Resolved runtime assembly knobs from API profile names."""

    profile: str
    backend_profile: str
    permission_profile: str
    tool_policy_profile: ToolPolicyProfile
    permissions: list[FilesystemPermission] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def resolve_runtime_assembly(
    *,
    profile: str | None = None,
    backend_profile: str | None = None,
    permission_profile: str | None = None,
    tool_policy_profile: str | None = None,
) -> RuntimeAssemblyConfig:
    """Resolve coarse API profile names into runtime controls."""
    normalized_profile = _runtime_profile(profile)
    normalized_backend = _backend_profile(backend_profile or _default_backend_profile(normalized_profile))
    normalized_permission = _permission_profile(permission_profile or _default_permission_profile(normalized_profile))
    normalized_tool_policy = _tool_policy_profile(
        tool_policy_profile or _default_tool_policy_profile(normalized_profile, normalized_permission)
    )
    return RuntimeAssemblyConfig(
        profile=normalized_profile,
        backend_profile=normalized_backend,
        permission_profile=normalized_permission,
        tool_policy_profile=normalized_tool_policy,
        permissions=_permissions(normalized_permission),
        warnings=_warnings(normalized_backend),
    )


def _runtime_profile(value: str | None) -> str:
    profile = value or "dev-default"
    if profile not in {"dev-default", "locked-down", "ci-eval"}:
        raise UnsupportedRuntimeAssemblyProfileError(_RUNTIME_PROFILE_KIND, profile)
    return profile


def _backend_profile(value: str) -> str:
    if value not in {"ephemeral", "workspace-readonly", "workspace-edit", "sandbox-exec", "store-backed"}:
        raise UnsupportedRuntimeAssemblyProfileError(_BACKEND_PROFILE_KIND, value)
    return value


def _permission_profile(value: str) -> str:
    if value not in {"workspace-readonly", "workspace-edit", "locked-down", "ci-eval"}:
        raise UnsupportedRuntimeAssemblyProfileError(_PERMISSION_PROFILE_KIND, value)
    return value


def _tool_policy_profile(value: str) -> ToolPolicyProfile:
    if value not in {"dev-default", "locked-down", "ci-eval"}:
        raise UnsupportedRuntimeAssemblyProfileError(_TOOL_POLICY_PROFILE_KIND, value)
    return cast("ToolPolicyProfile", value)


def _default_backend_profile(profile: str) -> str:
    return {"ci-eval": "workspace-readonly", "locked-down": "workspace-readonly"}.get(profile, "ephemeral")


def _default_permission_profile(profile: str) -> str:
    return {"ci-eval": "ci-eval", "locked-down": "locked-down"}.get(profile, "workspace-edit")


def _default_tool_policy_profile(profile: str, permission_profile: str) -> str:
    if profile == "ci-eval" or permission_profile == "ci-eval":
        return "ci-eval"
    if profile == "locked-down" or permission_profile in {"workspace-readonly", "locked-down"}:
        return "locked-down"
    return "dev-default"


def _permissions(permission_profile: str) -> list[FilesystemPermission]:
    if permission_profile in {"workspace-readonly", "locked-down", "ci-eval"}:
        return [FilesystemPermission(operations=["write"], paths=["/**"], mode="deny")]
    return []


def _warnings(backend_profile: str) -> list[str]:
    if backend_profile in {"sandbox-exec", "store-backed"}:
        return [f"Backend profile {backend_profile} is parsed but not fully backed by a production runtime yet."]
    return []
