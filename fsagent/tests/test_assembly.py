from collections.abc import Mapping

import pytest
from deepagents import FilesystemPermission

from fsagent.api.schemas import RunRequest
from fsagent.api.service import FsAgentApiService
from fsagent.runtime.assembly import resolve_runtime_assembly


def test_locked_down_permission_profile_denies_workspace_writes():
    config = resolve_runtime_assembly(permission_profile="workspace-readonly")

    assert config.tool_policy_profile == "locked-down"
    assert config.permissions == [FilesystemPermission(operations=["write"], paths=["/**"], mode="deny")]
    assert config.warnings == []


def test_ci_eval_profile_denies_side_effects_and_sets_tool_policy():
    config = resolve_runtime_assembly(profile="ci-eval")

    assert config.tool_policy_profile == "ci-eval"
    assert config.permissions == [FilesystemPermission(operations=["write"], paths=["/**"], mode="deny")]


@pytest.mark.parametrize("backend_profile", ["sandbox-exec", "store-backed"])
def test_parsed_only_backend_profiles_return_warning(backend_profile: str) -> None:
    config = resolve_runtime_assembly(backend_profile=backend_profile)

    assert config.backend_profile == backend_profile
    assert config.warnings == [
        f"Backend profile {backend_profile} is parsed but not fully backed by a production runtime yet."
    ]


def test_unknown_profile_name_raises_value_error():
    with pytest.raises(ValueError, match="Unsupported runtime profile: prod-root"):
        resolve_runtime_assembly(profile="prod-root")


async def test_default_service_path_uses_resolved_profiles(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_runtime_kwargs: dict[str, object] = {}
    captured_invoke_config: Mapping[str, object] | None = None
    assembly_resolve_count = 0

    class Runtime:
        async def ainvoke(
            self,
            _payload: object,
            config: Mapping[str, object] | None = None,
        ) -> dict[str, str]:
            nonlocal captured_invoke_config
            captured_invoke_config = config
            return {"final_response": "ok"}

    def fake_create_runtime(**kwargs: object) -> Runtime:
        captured_runtime_kwargs.update(kwargs)
        return Runtime()

    def fake_build_chat_qwen(_env: object, *, thinking: bool | None = None) -> str:
        return f"fake:model:{thinking}"

    def tracking_resolve_runtime_assembly(**kwargs: object) -> object:
        nonlocal assembly_resolve_count
        assembly_resolve_count += 1
        return resolve_runtime_assembly(**kwargs)

    monkeypatch.setattr("fsagent.api.service.create_runtime", fake_create_runtime)
    monkeypatch.setattr("fsagent.api.service.build_chat_qwen", fake_build_chat_qwen)
    monkeypatch.setattr("fsagent.api.service.resolve_runtime_assembly", tracking_resolve_runtime_assembly)

    await FsAgentApiService().create_run(
        RunRequest(mode="fast", message="hello", permissionProfile="workspace-readonly"),
    )

    assert assembly_resolve_count == 1
    assert captured_runtime_kwargs["tool_policy_profile"] == "locked-down"
    assert captured_runtime_kwargs["permissions"][0].operations == ["write"]
    assert captured_runtime_kwargs["permissions"][0].paths == ["/**"]
    assert captured_runtime_kwargs["permissions"][0].mode == "deny"
    assert captured_invoke_config is not None
    assert captured_invoke_config["metadata"]["tool_policy_profile"] == "locked-down"
