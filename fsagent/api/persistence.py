"""Session persistence adapters for API snapshots and resume metadata."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from fsagent.api.schemas import SessionResponse, TimelineEvent


class SessionStoreNotFoundError(KeyError):
    """Raised when a session store cannot find a session."""


@dataclass(slots=True)
class SessionStoreRecord:
    """Persisted session snapshot plus runtime resume metadata."""

    session: SessionResponse
    runtime_config: dict[str, object] = field(default_factory=dict)
    checkpoint_ref: str | None = None


class SessionStore(Protocol):
    """Store interface for session snapshots."""

    def create(self, record: SessionStoreRecord) -> None:
        """Create a stored session record."""

    def get(self, session_id: str) -> SessionStoreRecord:
        """Return a stored session record."""

    def update(self, record: SessionStoreRecord) -> None:
        """Update a stored session record."""

    def append_event(self, session_id: str, event: TimelineEvent) -> None:
        """Append a timeline event to a stored session."""

    def list(self) -> list[SessionStoreRecord]:
        """List stored session records."""


class InMemorySessionStore:
    """In-memory `SessionStore` implementation preserving current behavior."""

    def __init__(self) -> None:
        """Initialize an empty store."""
        self._records: dict[str, SessionStoreRecord] = {}

    def create(self, record: SessionStoreRecord) -> None:
        """Create a stored session record."""
        self._records[record.session.session_id] = _copy_record(record)

    def get(self, session_id: str) -> SessionStoreRecord:
        """Return a stored session record."""
        try:
            return _copy_record(self._records[session_id])
        except KeyError as exc:
            raise SessionStoreNotFoundError(session_id) from exc

    def update(self, record: SessionStoreRecord) -> None:
        """Update a stored session record."""
        self._records[record.session.session_id] = _copy_record(record)

    def append_event(self, session_id: str, event: TimelineEvent) -> None:
        """Append a timeline event to a stored session."""
        record = self.get(session_id)
        record.session.timeline.append(event.model_copy(deep=True))
        self.update(record)

    def list(self) -> list[SessionStoreRecord]:
        """List stored session records."""
        return [_copy_record(record) for record in self._records.values()]


class JsonlSessionStore:
    """JSONL-backed store for local restart recovery."""

    def __init__(self, path: str | Path) -> None:
        """Initialize the store and load any existing JSONL snapshots."""
        self._path = Path(path)
        self._records: dict[str, SessionStoreRecord] = {}
        self._load()

    def create(self, record: SessionStoreRecord) -> None:
        """Create a stored session record."""
        self._records[record.session.session_id] = _copy_record(record)
        self._append(record)

    def get(self, session_id: str) -> SessionStoreRecord:
        """Return a stored session record."""
        try:
            return _copy_record(self._records[session_id])
        except KeyError as exc:
            raise SessionStoreNotFoundError(session_id) from exc

    def update(self, record: SessionStoreRecord) -> None:
        """Update a stored session record."""
        self._records[record.session.session_id] = _copy_record(record)
        self._append(record)

    def append_event(self, session_id: str, event: TimelineEvent) -> None:
        """Append a timeline event to a stored session."""
        record = self.get(session_id)
        record.session.timeline.append(event.model_copy(deep=True))
        self.update(record)

    def list(self) -> list[SessionStoreRecord]:
        """List stored session records."""
        return [_copy_record(record) for record in self._records.values()]

    def _load(self) -> None:
        if not self._path.exists():
            return
        lines = self._path.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                record = _record_from_json(json.loads(line))
            except json.JSONDecodeError:
                if index == len(lines) - 1:
                    continue
                raise
            self._records[record.session.session_id] = record

    def _append(self, record: SessionStoreRecord) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(_record_to_json(record), ensure_ascii=False) + "\n")


def _copy_record(record: SessionStoreRecord) -> SessionStoreRecord:
    return SessionStoreRecord(
        session=record.session.model_copy(deep=True),
        runtime_config=dict(record.runtime_config),
        checkpoint_ref=record.checkpoint_ref,
    )


def _record_to_json(record: SessionStoreRecord) -> dict[str, object]:
    return {
        "session": record.session.model_dump(by_alias=True),
        "runtimeConfig": record.runtime_config,
        "checkpointRef": record.checkpoint_ref,
    }


def _record_from_json(payload: dict[str, object]) -> SessionStoreRecord:
    session = SessionResponse(**payload["session"])
    runtime_config = payload.get("runtimeConfig")
    checkpoint_ref = payload.get("checkpointRef")
    return SessionStoreRecord(
        session=session,
        runtime_config=dict(runtime_config) if isinstance(runtime_config, dict) else {},
        checkpoint_ref=str(checkpoint_ref) if checkpoint_ref is not None else None,
    )
