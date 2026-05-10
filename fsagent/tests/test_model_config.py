import os
import sys
import types
from typing import Any

import pytest

from fsagent.runtime.model_config import (
    FsAgentEnv,
    build_chat_qwen,
    load_env_file,
    load_model_catalog,
    load_model_settings,
    resolve_thinking_enabled,
    should_retry_empty_choices_error,
)


def test_load_env_file_reads_default_model_settings(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        """MODEL=Qwen/Qwen3.5-27B
BASE_URL=https://api-inference.modelscope.cn/v1
API_KEY=xxx
AVAILABLE_MODELS_JSON=model_config.json
""",
        encoding="utf-8",
    )

    env = load_env_file(env_path)

    assert env == {
        "MODEL": "Qwen/Qwen3.5-27B",
        "BASE_URL": "https://api-inference.modelscope.cn/v1",
        "API_KEY": "xxx",
        "AVAILABLE_MODELS_JSON": "model_config.json",
    }


def test_load_env_file_ignores_comment_after_quoted_api_key(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        'API_KEY="ms-valid-token" # local account note\n',
        encoding="utf-8",
    )

    env = load_env_file(env_path)

    assert env["API_KEY"] == "ms-valid-token"


def test_load_model_settings_uses_non_thinking_sampling_for_default_model(tmp_path):
    config_path = tmp_path / "model_config.json"
    config_path.write_text(
        """
        [
          {
            "name": "Qwen/Qwen3.5-27B",
            "default_thinking_enabled": false,
            "sampling": {
              "thinking": {"temperature": 1.0, "top_p": 0.95},
              "non_thinking": {
                "temperature": 0.7,
                "top_p": 0.8,
                "top_k": 20,
                "min_p": 0.0,
                "presence_penalty": 1.5,
                "repetition_penalty": 1.0
              }
            }
          }
        ]
        """,
        encoding="utf-8",
    )
    env = FsAgentEnv(
        model="Qwen/Qwen3.5-27B",
        base_url="https://api-inference.modelscope.cn/v1",
        api_key="xxx",
        available_models_json=str(config_path),
    )

    settings = load_model_settings(env)

    assert settings.model == "Qwen/Qwen3.5-27B"
    assert settings.base_url == "https://api-inference.modelscope.cn/v1"
    assert settings.api_key == "xxx"
    assert settings.enable_thinking is False
    assert settings.temperature == 0.7
    assert settings.top_p == 0.8
    assert settings.presence_penalty == 1.5
    assert settings.extra_body == {"top_k": 20, "min_p": 0.0, "repetition_penalty": 1.0}


def test_load_model_settings_uses_requested_thinking_sampling(tmp_path):
    config_path = tmp_path / "model_config.json"
    config_path.write_text(
        """
        [
          {
            "name": "Qwen/Qwen3.5-27B",
            "default_thinking_enabled": false,
            "thinking_switch_type": "toggle",
            "sampling": {
              "thinking": {"temperature": 1.0, "top_p": 0.95},
              "non_thinking": {"temperature": 0.7, "top_p": 0.8}
            }
          }
        ]
        """,
        encoding="utf-8",
    )
    env = FsAgentEnv(model="Qwen/Qwen3.5-27B", available_models_json=str(config_path))

    thinking = load_model_settings(env, thinking=True)
    non_thinking = load_model_settings(env, thinking=False)

    assert thinking.enable_thinking is True
    assert thinking.temperature == 1.0
    assert thinking.top_p == 0.95
    assert non_thinking.enable_thinking is False
    assert non_thinking.temperature == 0.7
    assert non_thinking.top_p == 0.8


def test_load_model_catalog_reads_display_and_sampling(tmp_path):
    config_path = tmp_path / "model_config.json"
    config_path.write_text(
        """
        [
          {
            "name": "Qwen/Qwen3.5-27B",
            "display_name": "Qwen3.5-27B",
            "supports_thinking": true,
            "default_thinking_enabled": false,
            "thinking_switch_type": "toggle",
            "sampling": {
              "thinking": {"temperature": 1.0},
              "non_thinking": {"temperature": 0.7}
            }
          }
        ]
        """,
        encoding="utf-8",
    )

    catalog = load_model_catalog(FsAgentEnv(available_models_json=str(config_path)))

    assert catalog == [
        {
            "name": "Qwen/Qwen3.5-27B",
            "display_name": "Qwen3.5-27B",
            "supports_thinking": True,
            "default_thinking_enabled": False,
            "thinking_switch_type": "toggle",
            "sampling": {
                "thinking": {"temperature": 1.0},
                "non_thinking": {"temperature": 0.7},
            },
        }
    ]


def test_resolve_thinking_enabled_uses_fixed_model_default(tmp_path):
    config_path = tmp_path / "model_config.json"
    config_path.write_text(
        """
        [
          {
            "name": "Qwen/Qwen3-30B-A3B-Thinking-2507",
            "default_thinking_enabled": true,
            "thinking_switch_type": "fixed"
          }
        ]
        """,
        encoding="utf-8",
    )

    enabled = resolve_thinking_enabled(
        FsAgentEnv(model="Qwen/Qwen3-30B-A3B-Thinking-2507", available_models_json=str(config_path)),
        requested=False,
    )

    assert enabled is True


def test_build_chat_qwen_imports_langchain_qwq_with_sampling(monkeypatch):
    captured: dict[str, object] = {}

    class FakeChatQwen:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    module = types.ModuleType("langchain_qwq")
    module.ChatQwen = FakeChatQwen
    monkeypatch.setitem(sys.modules, "langchain_qwq", module)

    model = build_chat_qwen(
        FsAgentEnv(
            model="Qwen/Qwen3.5-27B",
            base_url="https://api-inference.modelscope.cn/v1",
            api_key="xxx",
            available_models_json="missing.json",
        )
    )

    assert isinstance(model, FakeChatQwen)
    assert captured["model"] == "Qwen/Qwen3.5-27B"
    assert captured["base_url"] == "https://api-inference.modelscope.cn/v1"
    assert captured["api_key"] == "xxx"


def test_empty_choices_error_is_retryable():
    error = TypeError("Received response with null value for 'choices'. Full response keys: ['id', 'choices']")

    assert should_retry_empty_choices_error(error)


@pytest.mark.asyncio
async def test_build_chat_qwen_retries_transient_empty_choices_error(monkeypatch):
    calls = 0

    class FakeChatQwen:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def _agenerate(self, *_args: object, **_kwargs: object) -> dict[str, Any]:
            nonlocal calls
            calls += 1
            if calls == 1:
                msg = "Received response with null value for 'choices'. Full response keys: ['id']"
                raise TypeError(msg)
            return {"ok": True}

    module = types.ModuleType("langchain_qwq")
    module.ChatQwen = FakeChatQwen
    monkeypatch.setitem(sys.modules, "langchain_qwq", module)

    model = build_chat_qwen(
        FsAgentEnv(
            model="Qwen/Qwen3.5-27B",
            base_url="https://api-inference.modelscope.cn/v1",
            api_key="xxx",
            available_models_json="missing.json",
        )
    )

    result = await model._agenerate([])  # noqa: SLF001

    assert result == {"ok": True}
    assert calls == 2


def test_fsagent_env_prefers_process_environment(monkeypatch, tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("MODEL=from-file\nAPI_KEY=file-key\n", encoding="utf-8")
    monkeypatch.setenv("MODEL", "from-env")
    monkeypatch.setenv("API_KEY", "env-key")
    monkeypatch.setenv("BASE_URL", "https://api-inference.modelscope.cn/v1")

    env = FsAgentEnv.from_sources(env_path=env_path, environ=os.environ)

    assert env.model == "from-env"
    assert env.api_key == "env-key"
