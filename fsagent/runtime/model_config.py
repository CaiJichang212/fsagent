"""Model configuration helpers for the fsagent runtime."""

import json
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

DEFAULT_MODEL = "Qwen/Qwen3.5-27B"
DEFAULT_BASE_URL = "https://api-inference.modelscope.cn/v1"
DEFAULT_MODELS_JSON = "model_config.json"
EMPTY_CHOICES_MAX_ATTEMPTS = 2

logger = logging.getLogger(__name__)
T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class FsAgentEnv:
    """Environment configuration used to construct `ChatQwen`."""

    model: str = DEFAULT_MODEL
    base_url: str = DEFAULT_BASE_URL
    api_key: str | None = None
    available_models_json: str = DEFAULT_MODELS_JSON
    env_dir: Path | None = None

    @classmethod
    def from_sources(
        cls,
        *,
        env_path: str | Path | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> "FsAgentEnv":
        """Load configuration from `.env` and process environment.

        Process environment values take precedence over `.env` file values.

        Args:
            env_path: Optional `.env` path.
            environ: Environment mapping, defaults to `os.environ`.

        Returns:
            Resolved fsagent environment.
        """
        resolved_env_path = Path(env_path) if env_path is not None else default_env_path()
        file_values = load_env_file(resolved_env_path)
        source = environ if environ is not None else os.environ

        def value(name: str, default: str | None = None) -> str | None:
            return source.get(name) or file_values.get(name) or default

        models_json = value("AVAILABLE_MODELS_JSON", DEFAULT_MODELS_JSON) or DEFAULT_MODELS_JSON
        return cls(
            model=value("MODEL", DEFAULT_MODEL) or DEFAULT_MODEL,
            base_url=value("BASE_URL", DEFAULT_BASE_URL) or DEFAULT_BASE_URL,
            api_key=value("API_KEY"),
            available_models_json=models_json,
            env_dir=resolved_env_path.parent,
        )


@dataclass(frozen=True, slots=True)
class ModelSettings:
    """Resolved model constructor settings."""

    model: str
    base_url: str
    api_key: str | None
    enable_thinking: bool | None
    temperature: float | None
    top_p: float | None
    presence_penalty: float | None
    extra_body: dict[str, Any]


ModelCatalogEntry = dict[str, Any]


def default_env_path() -> Path:
    """Return the default `.env` path for this extension package."""
    return Path(__file__).resolve().parents[2] / ".env"


def load_env_file(path: str | Path) -> dict[str, str]:
    """Load a minimal dotenv file without adding a dependency.

    Args:
        path: Path to `.env`.

    Returns:
        Parsed key/value pairs. Missing files return an empty mapping.
    """
    env_path = Path(path)
    if not env_path.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        value = _parse_env_value(raw_value)
        values[key.strip()] = value
    return values


def load_model_settings(env: FsAgentEnv, *, thinking: bool | None = None) -> ModelSettings:
    """Resolve the selected model and best sampling settings.

    Args:
        env: fsagent environment values.
        thinking: Optional user-selected thinking mode for toggle-capable models.

    Returns:
        Model settings suitable for `ChatQwen`.
    """
    model_entry = _find_model_entry(env)
    enable_thinking = _enable_thinking(model_entry, requested=thinking)
    sampling = _select_sampling(model_entry, enable_thinking=enable_thinking)
    return ModelSettings(
        model=env.model,
        base_url=env.base_url,
        api_key=env.api_key,
        enable_thinking=enable_thinking,
        temperature=_optional_float(sampling.get("temperature")),
        top_p=_optional_float(sampling.get("top_p")),
        presence_penalty=_optional_float(sampling.get("presence_penalty")),
        extra_body=_extra_body(sampling),
    )


def load_model_catalog(env: FsAgentEnv | None = None) -> list[ModelCatalogEntry]:
    """Load frontend-visible model options from `model_config.json`."""
    resolved = env or FsAgentEnv.from_sources()
    path = _models_json_path(resolved)
    if path is None or not path.exists():
        return []
    raw = _read_models_json(path)
    return [_normalize_catalog_entry(entry) for entry in raw if isinstance(entry, dict) and entry.get("name")]


def resolve_thinking_enabled(env: FsAgentEnv, *, requested: bool | None) -> bool:
    """Resolve the effective thinking mode shown in sessions and sent to the model."""
    resolved = _enable_thinking(_find_model_entry(env), requested=requested)
    return requested if resolved is None and requested is not None else bool(resolved)


def build_chat_qwen(env: FsAgentEnv | None = None, *, thinking: bool | None = None) -> object:
    """Construct `langchain_qwq.ChatQwen` from fsagent configuration.

    Args:
        env: Optional explicit environment values.
        thinking: Optional user-selected thinking mode for toggle-capable models.

    Returns:
        Configured `ChatQwen` instance.
    """
    from langchain_qwq import ChatQwen  # noqa: PLC0415  # defer provider import until model construction

    settings = load_model_settings(env or FsAgentEnv.from_sources(), thinking=thinking)
    kwargs: dict[str, Any] = {
        "model": settings.model,
        "base_url": settings.base_url,
    }
    if settings.api_key:
        kwargs["api_key"] = settings.api_key
    if settings.enable_thinking is not None:
        kwargs["enable_thinking"] = settings.enable_thinking
    if settings.temperature is not None:
        kwargs["temperature"] = settings.temperature
    if settings.top_p is not None:
        kwargs["top_p"] = settings.top_p
    if settings.presence_penalty is not None:
        kwargs["presence_penalty"] = settings.presence_penalty
    if settings.extra_body:
        kwargs["extra_body"] = settings.extra_body
    resilient_chat_qwen = _with_empty_choices_retry(ChatQwen)
    return resilient_chat_qwen(**kwargs)


def should_retry_empty_choices_error(error: BaseException) -> bool:
    """Return whether an exception is the transient ModelScope empty choices response."""
    return (
        isinstance(error, TypeError)
        and "Received response with null value for 'choices'" in str(error)
        and "Full response keys" in str(error)
    )


def _find_model_entry(env: FsAgentEnv) -> Mapping[str, Any] | None:
    path = _models_json_path(env)
    if path is None or not path.exists():
        return None
    raw = _read_models_json(path)
    for entry in raw:
        if isinstance(entry, dict) and entry.get("name") == env.model:
            return entry
    return None


def _read_models_json(path: Path) -> list[object]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        msg = "Available models JSON must be a list."
        raise TypeError(msg)
    return raw


def _models_json_path(env: FsAgentEnv) -> Path | None:
    if not env.available_models_json:
        return None
    path = Path(env.available_models_json)
    if path.is_absolute():
        return path
    if env.env_dir is not None:
        return env.env_dir / path
    return default_env_path().parent / path


def _select_sampling(model_entry: Mapping[str, Any] | None, *, enable_thinking: bool | None) -> Mapping[str, Any]:
    if model_entry is None:
        return {}
    sampling = model_entry.get("sampling")
    if not isinstance(sampling, Mapping):
        return {}
    key = "thinking" if enable_thinking else "non_thinking"
    selected = sampling.get(key) or sampling.get("non_thinking") or sampling.get("thinking")
    return selected if isinstance(selected, Mapping) else {}


def _enable_thinking(model_entry: Mapping[str, Any] | None, *, requested: bool | None = None) -> bool | None:
    if model_entry is None:
        return None
    switch_type = str(model_entry.get("thinking_switch_type") or "")
    if switch_type == "toggle" and requested is not None:
        return requested
    raw = model_entry.get("default_thinking_enabled")
    return raw if isinstance(raw, bool) else None


def _normalize_catalog_entry(entry: Mapping[str, Any]) -> ModelCatalogEntry:
    return {
        "name": str(entry["name"]),
        "display_name": str(entry.get("display_name") or entry["name"]),
        "supports_thinking": bool(entry.get("supports_thinking", False)),
        "default_thinking_enabled": bool(entry.get("default_thinking_enabled", False)),
        "thinking_switch_type": str(entry.get("thinking_switch_type") or "fixed"),
        "sampling": _sampling_catalog(entry.get("sampling")),
    }


def _sampling_catalog(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): dict(sampling) for key, sampling in value.items() if isinstance(sampling, Mapping)}


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    return float(value)


def _extra_body(sampling: Mapping[str, Any]) -> dict[str, Any]:
    passthrough_keys = ("top_k", "min_p", "repetition_penalty")
    return {key: sampling[key] for key in passthrough_keys if key in sampling}


def _parse_env_value(raw_value: str) -> str:
    value = raw_value.strip()
    if not value:
        return ""
    if value[0] in {"'", '"'}:
        quote = value[0]
        end = value.find(quote, 1)
        if end != -1:
            return value[1:end]
        return value[1:]
    comment_start = value.find(" #")
    if comment_start != -1:
        value = value[:comment_start]
    return value.strip()


def _with_empty_choices_retry(chat_qwen_class: type[T]) -> type[T]:
    class EmptyChoicesRetryChatQwen(chat_qwen_class):  # type: ignore[misc, valid-type]
        def _generate(self, *args: object, **kwargs: object) -> object:
            last_error: TypeError | None = None
            for attempt in range(1, EMPTY_CHOICES_MAX_ATTEMPTS + 1):
                try:
                    return super()._generate(*args, **kwargs)  # type: ignore[misc]
                except TypeError as exc:
                    if not should_retry_empty_choices_error(exc) or attempt == EMPTY_CHOICES_MAX_ATTEMPTS:
                        raise
                    last_error = exc
                    _log_empty_choices_retry(attempt=attempt, error=exc)
            raise last_error or RuntimeError("Model retry loop exited unexpectedly.")

        async def _agenerate(self, *args: object, **kwargs: object) -> object:
            last_error: TypeError | None = None
            for attempt in range(1, EMPTY_CHOICES_MAX_ATTEMPTS + 1):
                try:
                    return await super()._agenerate(*args, **kwargs)  # type: ignore[misc]
                except TypeError as exc:
                    if not should_retry_empty_choices_error(exc) or attempt == EMPTY_CHOICES_MAX_ATTEMPTS:
                        raise
                    last_error = exc
                    _log_empty_choices_retry(attempt=attempt, error=exc)
            raise last_error or RuntimeError("Model retry loop exited unexpectedly.")

    return EmptyChoicesRetryChatQwen


def _log_empty_choices_retry(*, attempt: int, error: TypeError) -> None:
    logger.warning(
        "Model provider returned null choices; retrying request",
        extra={
            "event": "model.empty_choices.retry",
            "attempt": attempt,
            "max_attempts": EMPTY_CHOICES_MAX_ATTEMPTS,
            "error": str(error),
            "error_type": type(error).__name__,
        },
    )
