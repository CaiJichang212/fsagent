import json
import logging
from types import SimpleNamespace

from fastapi.testclient import TestClient

from fsagent.api.schemas import RunRequest
from fsagent.api.server import create_app
from fsagent.api.service import FsAgentApiService


class FakeRuntime:
    def __init__(self, outputs: list[dict[str, object]]) -> None:
        self.outputs = list(outputs)
        self.calls: list[tuple[object, object]] = []

    async def ainvoke(self, payload: object, config: object = None) -> dict[str, object]:
        self.calls.append((payload, config))
        return self.outputs.pop(0)


def _run_payload(mode: str = "fast", message: str = "hello") -> dict[str, object]:
    return {
        "mode": mode,
        "message": message,
        "model": "fake:model",
        "thinking": False,
        "mcpEnabled": False,
        "trustProjectMcp": False,
        "mcpConfigPath": "mcp.json",
    }


def _service(runtime: FakeRuntime) -> FsAgentApiService:
    def factory(_request: object) -> FakeRuntime:
        return runtime

    return FsAgentApiService(runtime_factory=factory)


def test_health_check_returns_ok():
    client = TestClient(create_app(_service(FakeRuntime([]))))

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_model_config_returns_available_models(monkeypatch, tmp_path):
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
    monkeypatch.setenv("AVAILABLE_MODELS_JSON", str(config_path))
    client = TestClient(create_app(_service(FakeRuntime([]))))

    response = client.get("/api/model-config")

    assert response.status_code == 200
    assert response.json()["models"] == [
        {
            "name": "Qwen/Qwen3.5-27B",
            "displayName": "Qwen3.5-27B",
            "supportsThinking": True,
            "defaultThinkingEnabled": False,
            "thinkingSwitchType": "toggle",
            "sampling": {
                "thinking": {"temperature": 1.0},
                "non_thinking": {"temperature": 0.7},
            },
        }
    ]


def test_create_fast_run_returns_completed_session():
    runtime = FakeRuntime([{"final_response": "fast result"}])
    service = _service(runtime)
    client = TestClient(create_app(service))

    response = client.post("/api/runs", json=_run_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "fast"
    assert body["status"] == "completed"
    assert body["message"] == "hello"
    assert body["finalResponse"] == "fast result"
    assert body["timeline"][-1]["kind"] == "run.completed"
    assert runtime.calls[0][0]["mode"] == "fast"


def test_create_plan_run_returns_review_session():
    interrupt = SimpleNamespace(
        value={
            "kind": "plan_review",
            "todos": [{"content": "Inspect files", "status": "pending"}],
            "plan_meta": {"goal": "Connect API", "assumptions": ["local dev"]},
            "allowed_actions": ["approve", "edit", "retry", "cancel"],
            "instructions": "Review the plan above.",
        }
    )
    runtime = FakeRuntime([{"__interrupt__": [interrupt]}])
    service = _service(runtime)
    client = TestClient(create_app(service))

    response = client.post("/api/runs", json=_run_payload(mode="plan", message="connect frontend"))

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "awaiting_plan_review"
    assert body["todos"] == [{"content": "Inspect files", "status": "pending"}]
    assert body["planMeta"]["goal"] == "Connect API"
    assert body["timeline"][-1]["kind"] == "interrupt.plan_review"


def test_get_run_returns_stored_session():
    runtime = FakeRuntime([{"final_response": "fast result"}])
    service = _service(runtime)
    client = TestClient(create_app(service))
    created = client.post("/api/runs", json=_run_payload()).json()

    response = client.get(f"/api/runs/{created['sessionId']}")

    assert response.status_code == 200
    assert response.json()["sessionId"] == created["sessionId"]
    assert response.json()["finalResponse"] == "fast result"


def test_review_approve_resumes_plan_and_returns_completed_session():
    interrupt = SimpleNamespace(
        value={
            "kind": "plan_review",
            "todos": [{"content": "Inspect files", "status": "pending"}],
            "plan_meta": {"goal": "Connect API"},
            "allowed_actions": ["approve", "edit", "retry", "cancel"],
            "instructions": "Review the plan above.",
        }
    )
    runtime = FakeRuntime(
        [
            {"__interrupt__": [interrupt]},
            {
                "todos": [{"content": "Inspect files", "status": "completed"}],
                "execution_log": [{"content": "Inspect files", "status": "completed", "result": "ok"}],
                "final_response": "# Result\nDone",
            },
        ]
    )
    service = _service(runtime)
    client = TestClient(create_app(service))
    created = client.post("/api/runs", json=_run_payload(mode="plan")).json()

    response = client.post(f"/api/runs/{created['sessionId']}/review", json={"action": "approve"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["finalResponse"] == "# Result\nDone"
    assert body["executionLog"] == [{"content": "Inspect files", "status": "completed", "result": "ok", "error": None}]
    assert runtime.calls[1][0].resume == {"action": "approve"}


def test_review_unknown_session_returns_404():
    service = _service(FakeRuntime([]))
    client = TestClient(create_app(service))

    response = client.post("/api/runs/missing/review", json={"action": "approve"})

    assert response.status_code == 404


class EventRuntime(FakeRuntime):
    async def ainvoke(self, payload: object, config: object = None) -> dict[str, object]:
        self.calls.append((payload, config))
        sink = (config.get("metadata") or {}).get("fsagent_event_sink")
        if sink is not None:
            await sink(
                {
                    "kind": "todo.started",
                    "message": "开始执行 Inspect files",
                    "todos": [{"content": "Inspect files", "status": "in_progress"}],
                    "execution_log": [],
                }
            )
            await sink(
                {
                    "kind": "todo.completed",
                    "message": "完成 Inspect files",
                    "todos": [{"content": "Inspect files", "status": "completed"}],
                    "execution_log": [{"content": "Inspect files", "status": "completed", "result": "ok"}],
                }
            )
        return self.outputs.pop(0)


class AgentEventRuntime(FakeRuntime):
    async def ainvoke(self, payload: object, config: object = None) -> dict[str, object]:
        self.calls.append((payload, config))
        sink = (config.get("metadata") or {}).get("fsagent_event_sink")
        if sink is not None:
            await sink(
                {
                    "kind": "agent.model.completed",
                    "message": "fast_runner 模型调用完成",
                    "agent_mode": "fast",
                    "phase": "fast_runner",
                    "turn_index": 1,
                    "duration_ms": 12.5,
                    "tool_call_count": 0,
                }
            )
        return self.outputs.pop(0)


def test_stream_fast_run_sends_agent_timeline_events():
    runtime = AgentEventRuntime([{"final_response": "fast streamed result"}])
    service = _service(runtime)
    client = TestClient(create_app(service))

    with client.stream("POST", "/api/runs/stream", json=_run_payload(mode="fast")) as response:
        body = "".join(response.iter_text())

    assert response.status_code == 200
    sessions = _sse_sessions(body)
    assert any(item["timeline"][-1]["kind"] == "agent.model.completed" for item in sessions)
    assert sessions[-1]["status"] == "completed"


def test_stream_create_run_sends_incremental_session_events():
    runtime = EventRuntime(
        [
            {
                "todos": [{"content": "Inspect files", "status": "completed"}],
                "execution_log": [{"content": "Inspect files", "status": "completed", "result": "ok"}],
                "final_response": "streamed result",
            }
        ]
    )
    service = _service(runtime)
    client = TestClient(create_app(service))

    with client.stream("POST", "/api/runs/stream", json=_run_payload(mode="plan")) as response:
        body = "".join(response.iter_text())

    assert response.status_code == 200
    sessions = _sse_sessions(body)
    assert sessions[0]["status"] == "planning"
    assert any(item["timeline"][-1]["kind"] == "todo.started" for item in sessions)
    assert any(item["timeline"][-1]["kind"] == "todo.completed" for item in sessions)
    assert sessions[-1]["status"] == "completed"
    assert sessions[-1]["finalResponse"] == "streamed result"


async def test_stream_create_run_logs_duplicate_progress_once():
    class DuplicateEventRuntime(FakeRuntime):
        async def ainvoke(self, payload: object, config: object = None) -> dict[str, object]:
            self.calls.append((payload, config))
            sink = (config.get("metadata") or {}).get("fsagent_event_sink")
            event = {
                "kind": "planner.started",
                "message": "开始生成计划",
                "todos": [],
                "execution_log": [],
            }
            await sink(event)
            await sink(event)
            return self.outputs.pop(0)

    runtime = DuplicateEventRuntime([{"final_response": "ok"}])
    service = _service(runtime)
    records: list[logging.LogRecord] = []

    class ListHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    service_logger = logging.getLogger("fsagent.api.service")
    previous_handlers = list(service_logger.handlers)
    previous_propagate = service_logger.propagate
    previous_level = service_logger.level
    service_logger.handlers = [ListHandler()]
    service_logger.propagate = False
    service_logger.setLevel(logging.INFO)
    try:
        snapshots = [session async for session in service.create_run_stream(RunRequest(**_run_payload(mode="plan")))]
    finally:
        service_logger.handlers = previous_handlers
        service_logger.propagate = previous_propagate
        service_logger.setLevel(previous_level)

    assert snapshots[-1].status == "completed"
    progress_logs = [record for record in records if getattr(record, "event", None) == "planner.started"]
    assert len(progress_logs) == 1


async def test_agent_progress_events_log_structured_metadata():
    runtime = AgentEventRuntime([{"final_response": "ok"}])
    service = _service(runtime)
    records: list[logging.LogRecord] = []

    class ListHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    service_logger = logging.getLogger("fsagent.api.service")
    previous_handlers = list(service_logger.handlers)
    previous_propagate = service_logger.propagate
    previous_level = service_logger.level
    service_logger.handlers = [ListHandler()]
    service_logger.propagate = False
    service_logger.setLevel(logging.INFO)
    try:
        snapshots = [session async for session in service.create_run_stream(RunRequest(**_run_payload(mode="fast")))]
    finally:
        service_logger.handlers = previous_handlers
        service_logger.propagate = previous_propagate
        service_logger.setLevel(previous_level)

    assert snapshots[-1].status == "completed"
    progress_logs = [record for record in records if getattr(record, "event", None) == "agent.model.completed"]
    assert len(progress_logs) == 1
    assert progress_logs[0].agent_mode == "fast"
    assert progress_logs[0].phase == "fast_runner"
    assert progress_logs[0].duration_ms == 12.5


async def test_planner_capability_warning_enters_timeline_and_structured_log():
    class CapabilityWarningRuntime(FakeRuntime):
        async def ainvoke(self, payload: object, config: object = None) -> dict[str, object]:
            self.calls.append((payload, config))
            sink = (config.get("metadata") or {}).get("fsagent_event_sink")
            await sink(
                {
                    "kind": "planner.capability_warning",
                    "message": "Caller-provided executor tools truncated to 20 items; +1 more omitted.",
                    "warning": "Caller-provided executor tools truncated to 20 items; +1 more omitted.",
                }
            )
            return self.outputs.pop(0)

    runtime = CapabilityWarningRuntime([{"final_response": "ok"}])
    service = _service(runtime)
    records: list[logging.LogRecord] = []

    class ListHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    service_logger = logging.getLogger("fsagent.api.service")
    previous_handlers = list(service_logger.handlers)
    previous_propagate = service_logger.propagate
    previous_level = service_logger.level
    service_logger.handlers = [ListHandler()]
    service_logger.propagate = False
    service_logger.setLevel(logging.INFO)
    try:
        snapshots = [session async for session in service.create_run_stream(RunRequest(**_run_payload(mode="plan")))]
    finally:
        service_logger.handlers = previous_handlers
        service_logger.propagate = previous_propagate
        service_logger.setLevel(previous_level)

    assert snapshots[-1].status == "completed"
    warning_events = [event for event in snapshots[-1].timeline if event.kind == "planner.capability_warning"]
    assert len(warning_events) == 1
    assert warning_events[0].message == "Caller-provided executor tools truncated to 20 items; +1 more omitted."
    progress_logs = [record for record in records if getattr(record, "event", None) == "planner.capability_warning"]
    assert len(progress_logs) == 1
    assert progress_logs[0].warning == "Caller-provided executor tools truncated to 20 items; +1 more omitted."


def test_stream_review_plan_sends_incremental_session_events():
    interrupt = SimpleNamespace(
        value={
            "kind": "plan_review",
            "todos": [{"content": "Inspect files", "status": "pending"}],
            "plan_meta": {"goal": "Connect API"},
            "instructions": "Review the plan above.",
        }
    )
    runtime = EventRuntime(
        [
            {"__interrupt__": [interrupt]},
            {
                "todos": [{"content": "Inspect files", "status": "completed"}],
                "execution_log": [{"content": "Inspect files", "status": "completed", "result": "ok"}],
                "final_response": "approved result",
            },
        ]
    )
    service = _service(runtime)
    client = TestClient(create_app(service))
    created = client.post("/api/runs", json=_run_payload(mode="plan")).json()

    with client.stream(
        "POST",
        f"/api/runs/{created['sessionId']}/review/stream",
        json={"action": "approve"},
    ) as response:
        body = "".join(response.iter_text())

    assert response.status_code == 200
    sessions = _sse_sessions(body)
    assert sessions[0]["status"] == "executing"
    assert any(item["timeline"][-1]["kind"] == "todo.started" for item in sessions)
    assert sessions[-1]["status"] == "completed"
    assert sessions[-1]["finalResponse"] == "approved result"


def _sse_sessions(body: str) -> list[dict[str, object]]:
    sessions = []
    for frame in body.strip().split("\n\n"):
        data = "\n".join(line[6:] for line in frame.splitlines() if line.startswith("data: "))
        if data:
            sessions.append(json.loads(data))
    return sessions
