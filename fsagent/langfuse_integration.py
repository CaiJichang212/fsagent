"""Optional Langfuse integration for fsagent runtime tracing."""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LangfuseSettings:
    """Resolved Langfuse settings for backend tracing."""

    enabled: bool = False
    public_key: str | None = None
    secret_key: str | None = None
    base_url: str = "https://cloud.langfuse.com"
    sample_rate: float | None = None
    debug: bool = False

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> LangfuseSettings:
        """Resolve Langfuse settings from environment variables."""
        values = os.environ if environ is None else environ
        explicit_enabled = _bool_env(values.get("FSAGENT_LANGFUSE_ENABLED"), default=False)
        public_key = _stripped(values.get("LANGFUSE_PUBLIC_KEY"))
        secret_key = _stripped(values.get("LANGFUSE_SECRET_KEY"))
        enabled = explicit_enabled and bool(public_key and secret_key)
        base_url = _stripped(values.get("LANGFUSE_BASE_URL")) or _stripped(values.get("LANGFUSE_HOST")) or cls.base_url
        sample_rate = None
        if enabled:
            try:
                sample_rate = _optional_float(values.get("LANGFUSE_SAMPLE_RATE"))
            except ValueError:
                enabled = False
        return cls(
            enabled=enabled,
            public_key=public_key,
            secret_key=secret_key,
            base_url=base_url,
            sample_rate=sample_rate if enabled else None,
            debug=_bool_env(values.get("LANGFUSE_DEBUG"), default=False),
        )


class LangfuseBridge:
    """Small boundary around Langfuse imports and lifecycle."""

    def __init__(self, *, settings: LangfuseSettings | None = None) -> None:
        """Initialize the bridge with explicit settings or environment settings."""
        self._settings = settings or LangfuseSettings.from_env()
        self._client: object | None = None

    @property
    def enabled(self) -> bool:
        """Return whether Langfuse tracing is enabled."""
        return self._settings.enabled

    def callback_handler(self) -> object | None:
        """Return a LangChain callback handler when tracing is enabled."""
        if not self.enabled:
            return None
        self._ensure_client()
        from langfuse.langchain import CallbackHandler  # noqa: PLC0415

        return CallbackHandler(public_key=self._settings.public_key)

    @contextmanager
    def observe_run(
        self,
        *,
        session_id: str,
        thread_id: str,
        mode: str,
        model: str,
        operation: str,
        input_payload: object,
    ) -> Iterator[object | None]:
        """Create a root Langfuse observation for one runtime invocation."""
        if not self.enabled:
            with nullcontext(None) as observation:
                yield observation
            return

        from langfuse import Langfuse, propagate_attributes  # noqa: PLC0415

        client = self._ensure_client()
        trace_id = Langfuse.create_trace_id(seed=session_id)
        metadata = {
            "thread_id": thread_id,
            "mode": mode,
            "model": model,
            "operation": operation,
        }
        with client.start_as_current_observation(
            as_type="span",
            name=f"fsagent.{mode}.{operation}",
            trace_context={"trace_id": trace_id},
        ) as observation:
            observation.update(input=_summarize_payload_for_langfuse(input_payload), metadata=metadata)
            with propagate_attributes(
                session_id=session_id,
                tags=["fsagent", mode, operation],
                metadata=metadata,
            ):
                yield observation

    def flush(self) -> None:
        """Flush queued Langfuse observations."""
        if self._client is not None:
            self._client.flush()

    def shutdown(self) -> None:
        """Shutdown the Langfuse singleton client."""
        if self._client is not None:
            self._client.shutdown()

    def _ensure_client(self) -> object:
        if self._client is not None:
            return self._client
        from langfuse import Langfuse  # noqa: PLC0415

        kwargs: dict[str, object] = {
            "public_key": self._settings.public_key,
            "secret_key": self._settings.secret_key,
            "base_url": self._settings.base_url,
            "debug": self._settings.debug,
        }
        if self._settings.sample_rate is not None:
            kwargs["sample_rate"] = self._settings.sample_rate
        self._client = Langfuse(**kwargs)
        return self._client


def _summarize_payload_for_langfuse(payload: object) -> dict[str, object]:
    """Return low-risk runtime input/output metadata for Langfuse."""
    if not isinstance(payload, Mapping):
        return {"type": type(payload).__name__}
    summary: dict[str, object] = {}
    mode = payload.get("mode")
    if mode is not None:
        summary["mode"] = str(mode)
    messages = payload.get("messages")
    if isinstance(messages, list):
        summary["message_count"] = len(messages)
    if "__interrupt__" in payload:
        summary["has_interrupt"] = True
    return summary or {"keys": sorted(str(key) for key in payload)}


def _stripped(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _bool_env(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _optional_float(value: str | None) -> float | None:
    stripped = _stripped(value)
    return float(stripped) if stripped is not None else None
