from fsagent.api.persistence import InMemorySessionStore, JsonlSessionStore, SessionStoreRecord
from fsagent.api.schemas import SessionResponse, TimelineEvent, TodoItem
from fsagent.api.server import _checkpointer_from_env, _session_store_from_env


def _session(status: str = "awaiting_plan_review") -> SessionResponse:
    return SessionResponse(
        sessionId="session-1",
        threadId="thread-1",
        mode="plan",
        status=status,
        message="hello",
        model="fake:model",
        thinking=False,
        mcpEnabled=False,
        trustProjectMcp=False,
        todos=[TodoItem(id="todo-001", content="Inspect files", status="pending")],
        timeline=[
            TimelineEvent(
                id="event-1",
                kind="session.created",
                message="created",
                at="2026-05-10T00:00:00+00:00",
            )
        ],
        createdAt="2026-05-10T00:00:00+00:00",
        updatedAt="2026-05-10T00:00:00+00:00",
    )


def test_in_memory_session_store_round_trip_preserves_snapshot_fields():
    store = InMemorySessionStore()
    record = SessionStoreRecord(session=_session(), runtime_config={"thread_id": "thread-1"}, checkpoint_ref="memory")

    store.create(record)
    stored = store.get("session-1")

    assert stored.session.status == "awaiting_plan_review"
    assert stored.session.todos[0].id == "todo-001"
    assert stored.session.timeline[0].fields == {}
    assert stored.runtime_config == {"thread_id": "thread-1"}
    assert stored.checkpoint_ref == "memory"


def test_jsonl_session_store_reloads_latest_snapshot(tmp_path):
    path = tmp_path / "sessions.jsonl"
    store = JsonlSessionStore(path)
    record = SessionStoreRecord(session=_session(), runtime_config={"thread_id": "thread-1"}, checkpoint_ref="memory")
    store.create(record)
    record.session.status = "cancelled"
    store.update(record)

    reloaded = JsonlSessionStore(path)

    assert reloaded.get("session-1").session.status == "cancelled"
    assert reloaded.get("session-1").runtime_config == {"thread_id": "thread-1"}


def test_jsonl_session_store_ignores_truncated_final_line(tmp_path):
    path = tmp_path / "sessions.jsonl"
    store = JsonlSessionStore(path)
    record = SessionStoreRecord(session=_session(), runtime_config={"thread_id": "thread-1"}, checkpoint_ref="memory")
    store.create(record)
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"session":')

    reloaded = JsonlSessionStore(path)

    assert reloaded.get("session-1").session.status == "awaiting_plan_review"
    assert reloaded.get("session-1").runtime_config == {"thread_id": "thread-1"}


def test_session_store_from_env_uses_jsonl_store_when_path_is_configured(tmp_path):
    path = tmp_path / "sessions.jsonl"

    store = _session_store_from_env({"FSAGENT_SESSION_STORE_PATH": str(path)})

    record = SessionStoreRecord(session=_session(), runtime_config={"thread_id": "thread-1"}, checkpoint_ref="memory")
    store.create(record)
    reloaded = JsonlSessionStore(path)
    assert reloaded.get("session-1").session.status == "awaiting_plan_review"


def test_session_store_from_env_defaults_to_in_memory_store():
    store = _session_store_from_env({})

    assert isinstance(store, InMemorySessionStore)


def test_checkpointer_from_env_defaults_to_none():
    assert _checkpointer_from_env({}) is None


def test_checkpointer_from_env_uses_configured_path(tmp_path):
    path = tmp_path / "checkpoints.sqlite"

    config = _checkpointer_from_env({"FSAGENT_CHECKPOINTER_PATH": str(path)})

    assert config is not None
    assert config.path == str(path)
