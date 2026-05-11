import json
import logging
from types import SimpleNamespace

from fastapi.testclient import TestClient

from fsagent.api.persistence import InMemorySessionStore
from fsagent.api.schemas import RunRequest, SessionResponse
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


def test_create_run_normalizes_legacy_contract_records_with_stable_ids():
    runtime = FakeRuntime(
        [
            {
                "todos": [{"content": "Inspect files", "status": "completed"}],
                "execution_log": [{"content": "Inspect files", "status": "completed", "result": "ok"}],
                "artifacts": ["Changed fsagent/api/schemas.py"],
                "final_response": "done",
            }
        ]
    )
    service = _service(runtime)
    client = TestClient(create_app(service))

    response = client.post("/api/runs", json=_run_payload(mode="plan"))

    assert response.status_code == 200
    body = response.json()
    assert body["todos"] == [
        {
            "id": "todo-001",
            "content": "Inspect files",
            "status": "completed",
            "risk": "low",
            "dependsOn": [],
            "evidenceIds": [],
            "verificationIds": [],
            "failureReason": None,
        }
    ]
    assert body["executionLog"] == [
        {
            "id": "log-001",
            "todoId": "todo-001",
            "content": "Inspect files",
            "status": "completed",
            "result": "ok",
            "error": None,
            "startedAt": None,
            "completedAt": None,
            "toolCallIds": [],
            "artifactIds": [],
            "verificationIds": [],
        }
    ]
    assert body["artifacts"] == [
        {
            "id": "artifact-001",
            "kind": "legacy",
            "path": None,
            "summary": "Changed fsagent/api/schemas.py",
            "size": None,
            "sha256": None,
            "redactionStatus": "none",
            "createdAt": None,
        }
    ]
    assert body["toolCalls"] == []
    assert body["evidence"] == []
    assert body["verification"] == []
    assert body["timeline"][0]["phase"] == "session"
    assert body["timeline"][0]["severity"] == "info"
    assert body["timeline"][0]["fields"]["mcp_enabled"] is False
    assert body["timeline"][0]["correlationId"] is None


def test_session_response_accepts_verifying_and_needs_revision_statuses():
    base = {
        "sessionId": "session-1",
        "threadId": "thread-1",
        "mode": "plan",
        "message": "hello",
        "model": "fake:model",
        "thinking": False,
        "mcpEnabled": False,
        "trustProjectMcp": False,
        "createdAt": "2026-05-10T00:00:00+00:00",
        "updatedAt": "2026-05-10T00:00:00+00:00",
    }

    assert SessionResponse(**base, status="verifying").status == "verifying"
    assert SessionResponse(**base, status="needs_revision").status == "needs_revision"


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
    assert body["todos"][0]["content"] == "Inspect files"
    assert body["todos"][0]["status"] == "pending"
    assert body["planMeta"]["goal"] == "Connect API"
    assert body["pendingReview"]["kind"] == "plan_review"
    assert body["pendingReview"]["status"] == "pending"
    assert body["pendingReview"]["allowedActions"] == ["approve", "edit", "retry", "cancel"]
    assert body["reviews"] == [body["pendingReview"]]
    assert body["timeline"][-1]["kind"] == "interrupt.plan_review"
    assert body["timeline"][-1]["correlationId"] == body["pendingReview"]["id"]


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
    assert body["executionLog"][0]["content"] == "Inspect files"
    assert body["executionLog"][0]["status"] == "completed"
    assert body["executionLog"][0]["result"] == "ok"
    assert body["executionLog"][0]["error"] is None
    assert body["pendingReview"] is None
    assert body["reviews"][0]["status"] == "approved"
    assert runtime.calls[1][0].resume == {"action": "approve"}


def test_generic_review_decision_resumes_matching_review_id():
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
    review_id = created["pendingReview"]["id"]

    response = client.post(
        f"/api/runs/{created['sessionId']}/reviews/{review_id}/decision",
        json={"reviewId": review_id, "action": "approve"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["pendingReview"] is None
    assert body["reviews"][0]["id"] == review_id
    assert body["reviews"][0]["status"] == "approved"
    assert runtime.calls[1][0].resume == {"action": "approve"}


def test_generic_review_decision_rejects_mismatched_review_id():
    interrupt = SimpleNamespace(
        value={
            "kind": "plan_review",
            "todos": [{"content": "Inspect files", "status": "pending"}],
            "plan_meta": {"goal": "Connect API"},
            "allowed_actions": ["approve", "edit", "retry", "cancel"],
            "instructions": "Review the plan above.",
        }
    )
    runtime = FakeRuntime([{"__interrupt__": [interrupt]}])
    service = _service(runtime)
    client = TestClient(create_app(service))
    created = client.post("/api/runs", json=_run_payload(mode="plan")).json()

    response = client.post(
        f"/api/runs/{created['sessionId']}/reviews/wrong-review/decision",
        json={"action": "approve"},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "Review id does not match the pending review."


def test_generic_plan_review_invalid_action_does_not_mutate_pending_review():
    interrupt = SimpleNamespace(
        value={
            "kind": "plan_review",
            "todos": [{"content": "Inspect files", "status": "pending"}],
            "plan_meta": {"goal": "Connect API"},
            "allowed_actions": ["approve", "edit", "retry", "cancel"],
            "instructions": "Review the plan above.",
        }
    )
    runtime = FakeRuntime([{"__interrupt__": [interrupt]}])
    service = _service(runtime)
    client = TestClient(create_app(service))
    created = client.post("/api/runs", json=_run_payload(mode="plan")).json()
    review_id = created["pendingReview"]["id"]

    response = client.post(
        f"/api/runs/{created['sessionId']}/reviews/{review_id}/decision",
        json={"action": "deny", "reason": "wrong action for plan review"},
    )
    stored = client.get(f"/api/runs/{created['sessionId']}").json()

    assert response.status_code == 409
    assert response.json()["detail"] == "Action deny is not allowed for review review-001."
    assert stored["status"] == "awaiting_plan_review"
    assert stored["pendingReview"]["id"] == review_id
    assert stored["pendingReview"]["status"] == "pending"
    assert stored["reviews"][0]["status"] == "pending"
    assert len(runtime.calls) == 1


def test_tool_interrupt_returns_awaiting_tool_review_not_completed():
    interrupt = SimpleNamespace(
        value={
            "kind": "tool_review",
            "risk": "high",
            "subject": {"toolCallId": "tool-001", "name": "execute"},
            "proposed_input_summary": "execute: uv run pytest",
            "allowed_actions": ["approve", "modify", "deny", "cancel"],
            "instructions": "Review high-risk tool call.",
        }
    )
    runtime = FakeRuntime([{"__interrupt__": [interrupt]}])
    service = _service(runtime)
    client = TestClient(create_app(service))

    response = client.post("/api/runs", json=_run_payload(mode="plan"))

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "awaiting_tool_review"
    assert body["finalResponse"] is None
    assert body["pendingReview"]["kind"] == "tool_review"
    assert body["pendingReview"]["risk"] == "high"
    assert body["pendingReview"]["proposedInputSummary"] == "execute: uv run pytest"
    assert body["timeline"][-1]["kind"] == "interrupt.tool_review"


def test_langchain_hitl_tool_interrupt_payload_becomes_review_record():
    interrupt = SimpleNamespace(
        value={
            "action_requests": [
                {
                    "name": "execute",
                    "args": {"command": "uv run pytest fsagent/tests -q"},
                    "description": "Tool execution requires approval\n\nTool: execute",
                }
            ],
            "review_configs": [{"action_name": "execute", "allowed_decisions": ["approve", "edit", "reject"]}],
        }
    )
    runtime = FakeRuntime([{"__interrupt__": [interrupt]}])
    service = _service(runtime)
    client = TestClient(create_app(service))

    response = client.post("/api/runs", json=_run_payload(mode="plan"))

    assert response.status_code == 200
    body = response.json()
    review = body["pendingReview"]
    assert body["status"] == "awaiting_tool_review"
    assert review["kind"] == "tool_review"
    assert review["risk"] == "high"
    assert review["allowedActions"] == ["approve", "modify", "deny", "cancel"]
    assert review["subject"]["actionRequests"][0]["name"] == "execute"
    assert review["subject"]["actionRequests"][0]["args"]["command"] == "uv run pytest fsagent/tests -q"
    assert "execute" in review["proposedInputSummary"]


def test_generic_tool_review_approve_resumes_all_langchain_hitl_actions():
    interrupt = SimpleNamespace(
        value={
            "action_requests": [
                {"name": "execute", "args": {"command": "pytest"}, "description": "Review execute"},
                {"name": "write_file", "args": {"path": "out.txt", "content": "ok"}, "description": "Review write"},
            ],
            "review_configs": [
                {"action_name": "execute", "allowed_decisions": ["approve", "edit", "reject"]},
                {"action_name": "write_file", "allowed_decisions": ["approve", "edit", "reject"]},
            ],
        }
    )
    runtime = FakeRuntime(
        [
            {"__interrupt__": [interrupt]},
            {"todos": [], "execution_log": [], "final_response": "approved"},
        ]
    )
    service = _service(runtime)
    client = TestClient(create_app(service))
    created = client.post("/api/runs", json=_run_payload(mode="plan")).json()
    review_id = created["pendingReview"]["id"]

    response = client.post(
        f"/api/runs/{created['sessionId']}/reviews/{review_id}/decision",
        json={"action": "approve"},
    )

    assert response.status_code == 200
    assert runtime.calls[1][0].resume == {"decisions": [{"type": "approve"}, {"type": "approve"}]}


def test_generic_tool_review_approve_rejects_per_action_disallowed_decision_without_mutating_review():
    interrupt = SimpleNamespace(
        value={
            "action_requests": [
                {"name": "execute", "args": {"command": "pytest"}, "description": "Review execute"},
                {"name": "write_file", "args": {"path": "out.txt", "content": "ok"}, "description": "Review write"},
            ],
            "review_configs": [
                {"action_name": "execute", "allowed_decisions": ["reject"]},
                {"action_name": "write_file", "allowed_decisions": ["approve", "reject"]},
            ],
        }
    )
    runtime = FakeRuntime(
        [
            {"__interrupt__": [interrupt]},
            {"final_response": "should not resume"},
        ]
    )
    service = _service(runtime)
    client = TestClient(create_app(service))
    created = client.post("/api/runs", json=_run_payload(mode="plan")).json()
    review_id = created["pendingReview"]["id"]

    response = client.post(
        f"/api/runs/{created['sessionId']}/reviews/{review_id}/decision",
        json={"action": "approve"},
    )
    stored = client.get(f"/api/runs/{created['sessionId']}").json()

    assert response.status_code == 409
    assert response.json()["detail"] == "Decision type approve is not allowed for reviewed tool action 1."
    assert stored["status"] == "awaiting_tool_review"
    assert stored["pendingReview"]["id"] == review_id
    assert stored["reviews"][0]["status"] == "pending"
    assert len(runtime.calls) == 1


def test_generic_tool_review_deny_rejects_per_action_disallowed_decision_without_mutating_review():
    interrupt = SimpleNamespace(
        value={
            "action_requests": [
                {"name": "execute", "args": {"command": "pytest"}, "description": "Review execute"},
                {"name": "write_file", "args": {"path": "out.txt", "content": "ok"}, "description": "Review write"},
            ],
            "review_configs": [
                {"action_name": "execute", "allowed_decisions": ["approve"]},
                {"action_name": "write_file", "allowed_decisions": ["approve", "reject"]},
            ],
        }
    )
    runtime = FakeRuntime(
        [
            {"__interrupt__": [interrupt]},
            {"final_response": "should not resume"},
        ]
    )
    service = _service(runtime)
    client = TestClient(create_app(service))
    created = client.post("/api/runs", json=_run_payload(mode="plan")).json()
    review_id = created["pendingReview"]["id"]

    response = client.post(
        f"/api/runs/{created['sessionId']}/reviews/{review_id}/decision",
        json={"action": "deny", "reason": "do not run"},
    )
    stored = client.get(f"/api/runs/{created['sessionId']}").json()

    assert response.status_code == 409
    assert response.json()["detail"] == "Decision type reject is not allowed for reviewed tool action 1."
    assert stored["status"] == "awaiting_tool_review"
    assert stored["pendingReview"]["id"] == review_id
    assert stored["reviews"][0]["status"] == "pending"
    assert len(runtime.calls) == 1


def test_generic_tool_review_respond_resumes_with_hitl_respond_decision():
    interrupt = SimpleNamespace(
        value={
            "action_requests": [
                {"name": "ask_user", "args": {"question": "Continue?"}, "description": "Review ask_user"},
            ],
            "review_configs": [{"action_name": "ask_user", "allowed_decisions": ["respond"]}],
        }
    )
    runtime = FakeRuntime(
        [
            {"__interrupt__": [interrupt]},
            {"final_response": "used response"},
        ]
    )
    service = _service(runtime)
    client = TestClient(create_app(service))
    created = client.post("/api/runs", json=_run_payload(mode="plan")).json()
    review_id = created["pendingReview"]["id"]

    response = client.post(
        f"/api/runs/{created['sessionId']}/reviews/{review_id}/decision",
        json={"action": "respond", "feedback": "Continue with cached data."},
    )

    assert response.status_code == 200
    assert runtime.calls[1][0].resume == {"decisions": [{"type": "respond", "message": "Continue with cached data."}]}


def test_generic_tool_review_deny_without_runtime_status_preserves_needs_revision():
    interrupt = SimpleNamespace(
        value={
            "action_requests": [
                {"name": "execute", "args": {"command": "pytest"}, "description": "Review execute"},
            ],
            "review_configs": [{"action_name": "execute", "allowed_decisions": ["approve", "reject"]}],
        }
    )
    runtime = FakeRuntime(
        [
            {"__interrupt__": [interrupt]},
            {"final_response": "tool call rejected"},
        ]
    )
    service = _service(runtime)
    client = TestClient(create_app(service))
    created = client.post("/api/runs", json=_run_payload(mode="plan")).json()
    review_id = created["pendingReview"]["id"]

    response = client.post(
        f"/api/runs/{created['sessionId']}/reviews/{review_id}/decision",
        json={"action": "deny", "reason": "do not run"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "needs_revision"
    assert body["pendingReview"] is None
    assert body["reviews"][0]["status"] == "denied"


def test_generic_tool_review_modify_rejects_partial_decision_list_without_mutating_review():
    interrupt = SimpleNamespace(
        value={
            "action_requests": [
                {"name": "execute", "args": {"command": "pytest"}, "description": "Review execute"},
                {"name": "write_file", "args": {"path": "out.txt", "content": "ok"}, "description": "Review write"},
            ],
            "review_configs": [
                {"action_name": "execute", "allowed_decisions": ["approve", "edit", "reject"]},
                {"action_name": "write_file", "allowed_decisions": ["approve", "edit", "reject"]},
            ],
        }
    )
    runtime = FakeRuntime([{"__interrupt__": [interrupt]}])
    service = _service(runtime)
    client = TestClient(create_app(service))
    created = client.post("/api/runs", json=_run_payload(mode="plan")).json()
    review_id = created["pendingReview"]["id"]

    response = client.post(
        f"/api/runs/{created['sessionId']}/reviews/{review_id}/decision",
        json={"action": "modify", "editedSubject": {"decisions": [{"type": "approve"}]}},
    )
    stored = client.get(f"/api/runs/{created['sessionId']}").json()

    assert response.status_code == 409
    assert response.json()["detail"] == "Edited subject must provide decisions for all reviewed tool actions."
    assert stored["status"] == "awaiting_tool_review"
    assert stored["pendingReview"]["id"] == review_id
    assert stored["pendingReview"]["status"] == "pending"
    assert stored["reviews"][0]["status"] == "pending"
    assert len(runtime.calls) == 1


def test_generic_tool_review_modify_rejects_invalid_decision_shape_without_mutating_review():
    interrupt = SimpleNamespace(
        value={
            "action_requests": [
                {"name": "execute", "args": {"command": "pytest"}, "description": "Review execute"},
            ],
            "review_configs": [{"action_name": "execute", "allowed_decisions": ["approve", "edit", "reject"]}],
        }
    )
    runtime = FakeRuntime([{"__interrupt__": [interrupt]}])
    service = _service(runtime)
    client = TestClient(create_app(service))
    created = client.post("/api/runs", json=_run_payload(mode="plan")).json()
    review_id = created["pendingReview"]["id"]

    response = client.post(
        f"/api/runs/{created['sessionId']}/reviews/{review_id}/decision",
        json={
            "action": "modify",
            "editedSubject": {"decisions": [{"type": "edit", "edited_action": {"name": "execute"}}]},
        },
    )
    stored = client.get(f"/api/runs/{created['sessionId']}").json()

    assert response.status_code == 409
    assert response.json()["detail"] == "Edited tool decision must include edited_action args."
    assert stored["pendingReview"]["id"] == review_id
    assert stored["reviews"][0]["status"] == "pending"
    assert len(runtime.calls) == 1


def test_generic_review_decision_stream_conflict_returns_409_before_stream_starts():
    interrupt = SimpleNamespace(
        value={
            "kind": "plan_review",
            "todos": [{"content": "Inspect files", "status": "pending"}],
            "plan_meta": {"goal": "Connect API"},
            "allowed_actions": ["approve", "edit", "retry", "cancel"],
            "instructions": "Review the plan above.",
        }
    )
    runtime = FakeRuntime([{"__interrupt__": [interrupt]}])
    service = _service(runtime)
    client = TestClient(create_app(service))
    created = client.post("/api/runs", json=_run_payload(mode="plan")).json()
    review_id = created["pendingReview"]["id"]

    with client.stream(
        "POST",
        f"/api/runs/{created['sessionId']}/reviews/{review_id}/decision/stream",
        json={"action": "deny", "reason": "wrong action for plan review"},
    ) as response:
        body = "".join(response.iter_text())
    stored = client.get(f"/api/runs/{created['sessionId']}").json()

    assert response.status_code == 409
    assert json.loads(body)["detail"] == "Action deny is not allowed for review review-001."
    assert body
    assert stored["pendingReview"]["id"] == review_id
    assert stored["reviews"][0]["status"] == "pending"
    assert len(runtime.calls) == 1


def test_runtime_needs_revision_status_and_verification_records_reach_api_snapshot():
    runtime = FakeRuntime(
        [
            {
                "status": "needs_revision",
                "verification": [{"id": "verification-001", "status": "failed", "command": "pytest", "exit_code": 1}],
                "final_response": "Verification failed.",
            }
        ]
    )
    service = _service(runtime)
    client = TestClient(create_app(service))

    response = client.post("/api/runs", json=_run_payload(mode="plan"))

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "needs_revision"
    assert body["verification"][0]["status"] == "failed"
    assert body["verification"][0]["command"] == "pytest"
    assert body["timeline"][-1]["kind"] == "run.needs_revision"
    assert body["timeline"][-1]["severity"] == "warning"


def test_review_unknown_session_returns_404():
    service = _service(FakeRuntime([]))
    client = TestClient(create_app(service))

    response = client.post("/api/runs/missing/review", json={"action": "approve"})

    assert response.status_code == 404


def test_review_after_restart_reports_missing_checkpoint_but_get_still_works():
    interrupt = SimpleNamespace(
        value={
            "kind": "plan_review",
            "todos": [{"content": "Inspect files", "status": "pending"}],
            "plan_meta": {"goal": "Connect API"},
            "allowed_actions": ["approve", "edit", "retry", "cancel"],
            "instructions": "Review the plan above.",
        }
    )
    store = InMemorySessionStore()
    first_service = FsAgentApiService(
        runtime_factory=lambda _request: FakeRuntime([{"__interrupt__": [interrupt]}]),
        session_store=store,
    )
    first_client = TestClient(create_app(first_service))
    created = first_client.post("/api/runs", json=_run_payload(mode="plan")).json()
    restarted_client = TestClient(
        create_app(
            FsAgentApiService(
                runtime_factory=lambda _request: FakeRuntime([]),
                session_store=store,
            )
        )
    )

    get_response = restarted_client.get(f"/api/runs/{created['sessionId']}")
    review_response = restarted_client.post(f"/api/runs/{created['sessionId']}/review", json={"action": "approve"})

    assert get_response.status_code == 200
    assert get_response.json()["status"] == "awaiting_plan_review"
    assert review_response.status_code == 409
    assert review_response.json()["detail"] == "Runtime checkpoint missing for session."


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


class ToolEventRuntime(FakeRuntime):
    async def ainvoke(self, payload: object, config: object = None) -> dict[str, object]:
        self.calls.append((payload, config))
        sink = (config.get("metadata") or {}).get("fsagent_event_sink")
        if sink is not None:
            await sink(
                {
                    "kind": "tool.policy_decision",
                    "message": "Tool policy review: execute",
                    "tool_calls": [
                        {
                            "id": "call-execute",
                            "name": "execute",
                            "risk": "high",
                            "status": "review_required",
                            "inputSummary": '{"command": "pytest"}',
                        }
                    ],
                }
            )
            await sink(
                {
                    "kind": "tool.policy_decision",
                    "message": "Tool policy review: write_file",
                    "tool_calls": [
                        {
                            "id": "call-write",
                            "name": "write_file",
                            "risk": "medium",
                            "status": "review_required",
                            "inputSummary": '{"path": "out.txt"}',
                        }
                    ],
                }
            )
        return self.outputs.pop(0)


class PartialToolUpdateRuntime(FakeRuntime):
    async def ainvoke(self, payload: object, config: object = None) -> dict[str, object]:
        self.calls.append((payload, config))
        sink = (config.get("metadata") or {}).get("fsagent_event_sink")
        if sink is not None:
            await sink(
                {
                    "kind": "tool.policy_decision",
                    "message": "Tool policy review: execute",
                    "tool_calls": [
                        {
                            "id": "call-execute",
                            "name": "execute",
                            "risk": "high",
                            "status": "review_required",
                            "inputSummary": '{"command": "pytest"}',
                        }
                    ],
                }
            )
            await sink(
                {
                    "kind": "tool.completed",
                    "message": "execute completed",
                    "tool_calls": [
                        {
                            "id": "call-execute",
                            "outputSummary": "pytest passed",
                        }
                    ],
                }
            )
        return self.outputs.pop(0)


class AgentToolLifecycleRuntime(FakeRuntime):
    async def ainvoke(self, payload: object, config: object = None) -> dict[str, object]:
        self.calls.append((payload, config))
        sink = (config.get("metadata") or {}).get("fsagent_event_sink")
        if sink is not None:
            await sink(
                {
                    "kind": "tool.policy_decision",
                    "message": "Tool policy review: execute",
                    "tool_calls": [
                        {
                            "id": "call-execute",
                            "name": "execute",
                            "risk": "high",
                            "status": "review_required",
                            "inputSummary": '{"command": "pytest"}',
                            "reviewId": "review-001",
                        }
                    ],
                }
            )
            await sink(
                {
                    "kind": "agent.tool.started",
                    "message": "execute started",
                    "tool_name": "execute",
                    "tool_call_id": "call-execute",
                }
            )
            await sink(
                {
                    "kind": "agent.tool.completed",
                    "message": "execute completed",
                    "tool_name": "execute",
                    "tool_call_id": "call-execute",
                    "duration_ms": 12.5,
                    "result_size_chars": 18,
                }
            )
        return self.outputs.pop(0)


def test_tool_progress_events_append_tool_calls_instead_of_replacing_history():
    runtime = ToolEventRuntime([{"final_response": "ok"}])
    service = _service(runtime)
    client = TestClient(create_app(service))

    response = client.post("/api/runs", json=_run_payload(mode="fast"))

    assert response.status_code == 200
    body = response.json()
    assert [tool_call["id"] for tool_call in body["toolCalls"]] == ["call-execute", "call-write"]


def test_tool_progress_partial_updates_preserve_existing_audit_fields():
    runtime = PartialToolUpdateRuntime([{"final_response": "ok"}])
    service = _service(runtime)
    client = TestClient(create_app(service))

    response = client.post("/api/runs", json=_run_payload(mode="fast"))

    assert response.status_code == 200
    body = response.json()
    assert body["toolCalls"] == [
        {
            "id": "call-execute",
            "todoId": None,
            "name": "execute",
            "risk": "high",
            "status": "review_required",
            "inputSummary": '{"command": "pytest"}',
            "outputSummary": "pytest passed",
            "reviewId": None,
            "durationMs": None,
        }
    ]


def test_agent_tool_lifecycle_events_update_existing_tool_call_status():
    runtime = AgentToolLifecycleRuntime([{"final_response": "ok"}])
    service = _service(runtime)
    client = TestClient(create_app(service))

    response = client.post("/api/runs", json=_run_payload(mode="fast"))

    assert response.status_code == 200
    body = response.json()
    assert body["toolCalls"] == [
        {
            "id": "call-execute",
            "todoId": None,
            "name": "execute",
            "risk": "high",
            "status": "completed",
            "inputSummary": '{"command": "pytest"}',
            "outputSummary": "result_size_chars=18",
            "reviewId": "review-001",
            "durationMs": 12.5,
        }
    ]


def test_final_runtime_result_without_execution_log_preserves_streamed_log():
    runtime = EventRuntime(
        [
            {
                "todos": [{"content": "Inspect files", "status": "completed"}],
                "final_response": "ok",
            }
        ]
    )
    service = _service(runtime)
    client = TestClient(create_app(service))

    response = client.post("/api/runs", json=_run_payload(mode="plan"))

    assert response.status_code == 200
    body = response.json()
    assert body["executionLog"] == [
        {
            "id": "log-001",
            "todoId": "todo-001",
            "content": "Inspect files",
            "status": "completed",
            "result": "ok",
            "error": None,
            "startedAt": None,
            "completedAt": None,
            "toolCallIds": [],
            "artifactIds": [],
            "verificationIds": [],
        }
    ]


def test_hitl_interrupt_emits_policy_audit_event_before_tool_decision():
    interrupt = SimpleNamespace(
        value={
            "action_requests": [
                {
                    "name": "execute",
                    "args": {"command": "pytest"},
                    "description": "Review execute",
                }
            ],
            "review_configs": [{"action_name": "execute", "allowed_decisions": ["approve", "edit", "reject"]}],
        }
    )
    runtime = FakeRuntime([{"__interrupt__": [interrupt]}])
    service = _service(runtime)
    client = TestClient(create_app(service))
    payload = {**_run_payload(mode="fast"), "toolPolicyProfile": "locked-down"}

    response = client.post("/api/runs", json=payload)

    assert response.status_code == 200
    body = response.json()
    policy_events = [event for event in body["timeline"] if event["kind"] == "tool.policy_decision"]
    assert policy_events
    assert policy_events[0]["fields"]["policyDecision"] == "review"
    assert policy_events[0]["fields"]["profile"] == "locked-down"
    assert body["toolCalls"][0]["name"] == "execute"
    assert body["toolCalls"][0]["status"] == "review_required"


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
