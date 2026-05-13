from types import SimpleNamespace

from fastapi.testclient import TestClient

from fsagent.api.server import create_app
from fsagent.tests.test_api import FakeRuntime, _run_payload, _service


def test_eval_plan_review_prevents_side_effect_before_approval():
    interrupt = SimpleNamespace(
        value={
            "kind": "plan_review",
            "todos": [{"content": "Write result file", "status": "pending"}],
            "plan_meta": {"goal": "Produce output"},
            "allowed_actions": ["approve", "edit", "retry", "cancel"],
            "instructions": "Review the plan before execution.",
        }
    )
    runtime = FakeRuntime(
        [
            {"__interrupt__": [interrupt]},
            {
                "todos": [{"content": "Write result file", "status": "completed"}],
                "execution_log": [{"content": "Write result file", "status": "completed", "result": "wrote file"}],
                "final_response": "side effect completed",
            },
        ]
    )
    client = TestClient(create_app(_service(runtime)))

    response = client.post("/api/runs", json=_run_payload(mode="plan", message="write a result file"))

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "awaiting_plan_review"
    assert body["finalResponse"] is None
    assert body["pendingReview"]["kind"] == "plan_review"
    assert body["todos"][0]["status"] == "pending"
    assert body["executionLog"] == []
    assert len(runtime.calls) == 1


def test_eval_tool_review_deny_does_not_report_completed():
    interrupt = SimpleNamespace(
        value={
            "action_requests": [
                {
                    "name": "execute",
                    "args": {"command": "touch /tmp/fsagent-eval-side-effect"},
                    "description": "Tool execution requires approval",
                }
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
    client = TestClient(create_app(_service(runtime)))
    created = client.post("/api/runs", json=_run_payload(mode="plan")).json()
    review_id = created["pendingReview"]["id"]

    response = client.post(
        f"/api/runs/{created['sessionId']}/reviews/{review_id}/decision",
        json={"action": "deny", "reason": "do not run side effects"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "needs_revision"
    assert body["status"] != "completed"
    assert body["pendingReview"] is None
    assert body["reviews"][0]["status"] == "denied"
    assert runtime.calls[1][0].resume == {"decisions": [{"type": "reject", "message": "do not run side effects"}]}
