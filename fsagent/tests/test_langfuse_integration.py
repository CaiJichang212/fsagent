import sys
import types
from typing import ClassVar

import pytest

from fsagent.langfuse_integration import (
    LangfuseBridge,
    LangfuseSettings,
    _summarize_payload_for_langfuse,
)


def test_langfuse_settings_disable_without_keys():
    settings = LangfuseSettings.from_env(
        {
            "FSAGENT_LANGFUSE_ENABLED": "true",
            "LANGFUSE_BASE_URL": "http://127.0.0.1:13000",
        }
    )

    assert not settings.enabled
    assert settings.base_url == "http://127.0.0.1:13000"


def test_langfuse_settings_enable_with_required_keys():
    environ = {
        "FSAGENT_LANGFUSE_ENABLED": "true",
        "LANGFUSE_PUBLIC_KEY": "pk-lf-test",
        "LANGFUSE_SECRET_KEY": "sk-lf-test",
        "LANGFUSE_BASE_URL": "http://127.0.0.1:13000",
        "LANGFUSE_SAMPLE_RATE": "0.25",
        "LANGFUSE_DEBUG": "true",
    }
    settings = LangfuseSettings.from_env(environ)

    assert settings.enabled
    assert settings.public_key == "pk-lf-test"
    assert settings.secret_key == environ["LANGFUSE_SECRET_KEY"]
    assert settings.base_url == "http://127.0.0.1:13000"
    assert settings.sample_rate == 0.25
    assert settings.debug is True


def test_langfuse_settings_ignore_invalid_sample_rate_when_disabled():
    settings = LangfuseSettings.from_env(
        {
            "FSAGENT_LANGFUSE_ENABLED": "false",
            "LANGFUSE_SAMPLE_RATE": "abc",
        }
    )

    assert not settings.enabled
    assert settings.sample_rate is None


def test_langfuse_settings_disable_when_enabled_sample_rate_is_invalid():
    environ = {
        "FSAGENT_LANGFUSE_ENABLED": "true",
        "LANGFUSE_PUBLIC_KEY": "pk-lf-test",
        "LANGFUSE_SECRET_KEY": "sk-lf-test",
        "LANGFUSE_SAMPLE_RATE": "abc",
    }

    settings = LangfuseSettings.from_env(environ)

    assert not settings.enabled
    assert settings.sample_rate is None


def test_disabled_bridge_returns_no_callback_and_null_observation():
    bridge = LangfuseBridge(settings=LangfuseSettings(enabled=False))

    assert bridge.callback_handler() is None
    with bridge.observe_run(
        session_id="session-1",
        thread_id="thread-1",
        mode="fast",
        model="fake:model",
        operation="invoke",
        input_payload={"messages": ["secret"]},
    ) as observation:
        assert observation is None


def test_summarize_payload_redacts_message_content():
    summary = _summarize_payload_for_langfuse({"mode": "fast", "messages": ["secret message"]})

    assert summary == {"mode": "fast", "message_count": 1}
    assert "secret message" not in str(summary)


class FakeObservation:
    calls: ClassVar[dict[str, object]]

    def __init__(self) -> None:
        self.updates: list[dict[str, object]] = []

    def update(self, **kwargs: object) -> None:
        self.updates.append(kwargs)


class FakeObservationContext:
    calls: ClassVar[dict[str, object]]

    def __init__(self, **kwargs: object) -> None:
        self.calls["start_kwargs"] = kwargs
        self.observation = FakeObservation()

    def __enter__(self) -> FakeObservation:
        self.calls["observation_entered"] = True
        return self.observation

    def __exit__(self, *args: object) -> bool:
        self.calls["observation_exited"] = True
        return False


class FakePropagationContext:
    calls: ClassVar[dict[str, object]]

    def __init__(self, **kwargs: object) -> None:
        self.calls["propagate_kwargs"] = kwargs

    def __enter__(self) -> None:
        self.calls["propagate_entered"] = True

    def __exit__(self, *args: object) -> bool:
        self.calls["propagate_exited"] = True
        return False


class FakeLangfuse:
    calls: ClassVar[dict[str, object]]

    def __init__(self, **kwargs: object) -> None:
        self.calls["client_kwargs"] = kwargs
        self.calls["client"] = self
        self.flushed = False
        self.shutdown_called = False

    @classmethod
    def create_trace_id(cls, *, seed: str) -> str:
        cls.calls["trace_seed"] = seed
        return f"trace-{seed}"

    def start_as_current_observation(self, **kwargs: object) -> FakeObservationContext:
        return FakeObservationContext(**kwargs)

    def flush(self) -> None:
        self.flushed = True

    def shutdown(self) -> None:
        self.shutdown_called = True


class FakeCallbackHandler:
    calls: ClassVar[dict[str, object]]

    def __init__(self, *, public_key: str | None = None, trace_context: object | None = None) -> None:
        self.calls["callback_kwargs"] = {
            "public_key": public_key,
            "trace_context": trace_context,
        }


def fake_propagate_attributes(**kwargs: object) -> FakePropagationContext:
    return FakePropagationContext(**kwargs)


def install_fake_langfuse_sdk(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    calls: dict[str, object] = {}
    for fake_type in (
        FakeObservation,
        FakeObservationContext,
        FakePropagationContext,
        FakeLangfuse,
        FakeCallbackHandler,
    ):
        fake_type.calls = calls

    langfuse_module = types.ModuleType("langfuse")
    langchain_module = types.ModuleType("langfuse.langchain")
    langfuse_module.Langfuse = FakeLangfuse
    langfuse_module.propagate_attributes = fake_propagate_attributes
    langchain_module.CallbackHandler = FakeCallbackHandler
    monkeypatch.setitem(sys.modules, "langfuse", langfuse_module)
    monkeypatch.setitem(sys.modules, "langfuse.langchain", langchain_module)
    return calls


def test_enabled_bridge_uses_fake_langfuse_sdk(monkeypatch):
    calls = install_fake_langfuse_sdk(monkeypatch)
    environ = {
        "FSAGENT_LANGFUSE_ENABLED": "true",
        "LANGFUSE_PUBLIC_KEY": "pk-lf-test",
        "LANGFUSE_SECRET_KEY": "sk-lf-test",
        "LANGFUSE_BASE_URL": "http://127.0.0.1:13000",
        "LANGFUSE_SAMPLE_RATE": "0.5",
        "LANGFUSE_DEBUG": "true",
    }

    bridge = LangfuseBridge(settings=LangfuseSettings.from_env(environ))

    handler = bridge.callback_handler()

    assert isinstance(handler, FakeCallbackHandler)
    assert calls["client_kwargs"] == {
        "public_key": "pk-lf-test",
        "secret_key": environ["LANGFUSE_SECRET_KEY"],
        "base_url": "http://127.0.0.1:13000",
        "debug": True,
        "sample_rate": 0.5,
    }
    assert calls["callback_kwargs"] == {"public_key": "pk-lf-test", "trace_context": None}

    with bridge.observe_run(
        session_id="session-1",
        thread_id="thread-1",
        mode="plan",
        model="fake:model",
        operation="invoke",
        input_payload={"mode": "plan", "messages": ["secret message"]},
    ) as observation:
        assert isinstance(observation, FakeObservation)

    assert calls["trace_seed"] == "session-1"
    assert calls["start_kwargs"] == {
        "as_type": "span",
        "name": "fsagent.plan.invoke",
        "trace_context": {"trace_id": "trace-session-1"},
    }
    metadata = {
        "thread_id": "thread-1",
        "mode": "plan",
        "model": "fake:model",
        "operation": "invoke",
    }
    assert observation.updates == [
        {
            "input": {"mode": "plan", "message_count": 1},
            "metadata": metadata,
        }
    ]
    assert calls["propagate_kwargs"] == {
        "session_id": "session-1",
        "tags": ["fsagent", "plan", "invoke"],
        "metadata": metadata,
    }
    assert calls["observation_entered"] is True
    assert calls["observation_exited"] is True
    assert calls["propagate_entered"] is True
    assert calls["propagate_exited"] is True

    bridge.flush()
    bridge.shutdown()

    client = calls["client"]
    assert client.flushed is True
    assert client.shutdown_called is True
