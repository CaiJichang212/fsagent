"""Structured logging helpers for fsagent runtime monitoring."""

from __future__ import annotations

import json
import logging
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Literal

LogMode = Literal["fast", "plan"]

_RESERVED_LOG_RECORD_KEYS = set(logging.makeLogRecord({}).__dict__) | {"asctime", "message"}
_DEDUPE_IGNORED_FIELDS = {"timeline_count"}
_CONFIGURED = False


@dataclass(frozen=True, slots=True)
class RunLogContext:
    """Stable context attached to every log entry for one run."""

    session_id: str
    thread_id: str
    mode: LogMode
    model: str


class JsonLogFormatter(logging.Formatter):
    """Format stdlib log records as newline-delimited JSON."""

    def format(self, record: logging.LogRecord) -> str:
        """Return one JSON object for a log record."""
        payload: dict[str, object] = {
            "at": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED_LOG_RECORD_KEYS and not key.startswith("_"):
                payload[key] = _normalize_value(value)
        if record.exc_info is not None:
            payload["traceback"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class RunLogger:
    """Session-scoped structured logger with duplicate suppression."""

    def __init__(self, *, logger: logging.Logger, context: RunLogContext) -> None:
        """Initialize a logger for one backend run."""
        self._logger = logger
        self._context = context
        self._seen: set[str] = set()

    def event(self, event: str, message: str, **fields: object) -> bool:
        """Write an info-level event unless the same payload was already logged."""
        return self._log(logging.INFO, event, message, fields)

    def exception(self, event: str, message: str, error: Exception, **fields: object) -> bool:
        """Write an error-level event with exception details unless duplicated."""
        return self._log(
            logging.ERROR,
            event,
            message,
            {
                **fields,
                "error": str(error),
                "error_type": type(error).__name__,
            },
            exc_info=(type(error), error, error.__traceback__),
        )

    def _log(
        self,
        level: int,
        event: str,
        message: str,
        fields: Mapping[str, object],
        *,
        exc_info: tuple[type[BaseException], BaseException, object] | None = None,
    ) -> bool:
        payload = {
            "event": event,
            "session_id": self._context.session_id,
            "thread_id": self._context.thread_id,
            "mode": self._context.mode,
            "model": self._context.model,
            **fields,
        }
        fingerprint = _fingerprint(event=event, message=message, fields=payload)
        if fingerprint in self._seen:
            return False
        self._seen.add(fingerprint)
        self._logger.log(level, message, extra=payload, exc_info=exc_info)
        return True


def configure_logging(*, level: str | None = None, log_file: str | None = None, force: bool = False) -> None:
    """Configure fsagent logging with a JSON formatter.

    Environment variables:
        FSAGENT_LOG_LEVEL: logging level, defaults to INFO.
        FSAGENT_LOG_FILE: optional file path. Defaults to stdout.
    """
    global _CONFIGURED  # noqa: PLW0603

    if _CONFIGURED and not force:
        return

    logger = logging.getLogger("fsagent")
    if force:
        logger.handlers.clear()
    elif logger.handlers:
        _CONFIGURED = True
        return

    resolved_level = _resolve_level(level or os.getenv("FSAGENT_LOG_LEVEL", "INFO"))
    resolved_log_file = log_file or os.getenv("FSAGENT_LOG_FILE")
    handler: logging.Handler
    if resolved_log_file:
        log_path = Path(resolved_log_file).expanduser()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(log_path, encoding="utf-8")
    else:
        handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonLogFormatter())
    logger.addHandler(handler)
    logger.setLevel(resolved_level)
    logger.propagate = False
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a stdlib logger under the fsagent namespace."""
    return logging.getLogger(name)


def _resolve_level(level: str) -> int:
    resolved = logging.getLevelName(level.upper())
    if isinstance(resolved, int):
        return resolved
    return logging.INFO


def _fingerprint(*, event: str, message: str, fields: Mapping[str, object]) -> str:
    payload = {
        "event": event,
        "message": message,
        "fields": _normalize_value(
            {key: value for key, value in fields.items() if key not in _DEDUPE_IGNORED_FIELDS}
        ),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return sha256(raw.encode("utf-8")).hexdigest()


def _normalize_value(value: object) -> object:
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    if isinstance(value, Mapping):
        return {str(key): _normalize_value(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, Sequence):
        return [_normalize_value(item) for item in value]
    return str(value)
