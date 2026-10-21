"""Regression tests for evidence-gated workflow outcome submission."""
import copy

import pytest

from learning.learning_client import LearningClient
from tools.record_workflow_outcome import submit_outcome, validate_payload


VALID = {
    "run_id": "run-123",
    "task_id": "task-123",
    "task_type": "code-review",
    "model": "sonnet",
    "rating": 4,
    "tokens": 123,
    "cost": 0.01,
    "evidence": {
        "kind": "test-report",
        "reference": "https://example.invalid/runs/123",
        "summary": "The regression suite passed and the reviewed defect is covered.",
    },
}


class FakeLearningClient:
    def __init__(self, artifact_response=None, outcome_response=True):
        self.artifact_response = artifact_response or {
            "ok": True,
            "artifact_status": "stored",
        }
        self.outcome_response = outcome_response
        self.artifacts = []
        self.outcomes = []
        self.calls = []

    def record_artifact(self, artifact, request_id=None):
        self.artifacts.append((copy.deepcopy(artifact), request_id))
        self.calls.append(("artifact", request_id))
        return self.artifact_response

    def process_outcome(self, **kwargs):
        self.outcomes.append(kwargs)
        self.calls.append(("outcome", kwargs["request_id"]))
        return self.outcome_response


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("rating", 0),
        ("rating", 6),
        ("rating", True),
        ("tokens", -1),
        ("tokens", False),
        ("cost", float("nan")),
        ("cost", -0.1),
        pytest.param("cost", 10**400, id="overflow-cost"),
        ("evidence", None),
        ("run_id", " "),
    ],
)
def test_rejects_invalid_or_unsubstantiated_outcomes(field, value):
    payload = {**VALID, field: value}
    with pytest.raises(ValueError):
        validate_payload(payload)


def test_requires_evidence_fields():
    payload = copy.deepcopy(VALID)
    payload["evidence"] = {"kind": "test-report", "reference": "run-123"}
    with pytest.raises(ValueError, match="evidence.summary"):
        validate_payload(payload)


def test_submits_evidence_artifact_before_learning_outcome():
    client = FakeLearningClient()

    result = submit_outcome(VALID, client)

    assert result == {
        "ok": True,
        "run_id": "run-123",
        "task_id": "task-123",
        "artifact_status": "stored",
        "learning_outcome": "acknowledged",
    }
    assert client.calls == [("artifact", "run-123"), ("outcome", "run-123")]
    assert len(client.artifacts) == 1
    artifact, request_id = client.artifacts[0]
    assert request_id == "run-123"
    assert artifact["artifact_type"] == "workflow-outcome"
    assert artifact["payload"]["run_id"] == "run-123"
    assert artifact["provenance"]["evidence"] == VALID["evidence"]
    assert client.outcomes == [
        {
            "task_id": "task-123",
            "task_type": "code-review",
            "model": "sonnet",
            "rating": 4,
            "tokens": 123,
            "cost": 0.01,
            "request_id": "run-123",
        }
    ]


def test_retry_uses_stable_artifact_content_and_identity():
    client = FakeLearningClient({"ok": True, "artifact_status": "duplicate"})

    first = submit_outcome(VALID, client)
    second = submit_outcome(VALID, client)

    assert first["artifact_status"] == second["artifact_status"] == "duplicate"
    assert client.artifacts[0] == client.artifacts[1]
    assert len(client.outcomes) == 2
    assert client.outcomes[0]["task_id"] == client.outcomes[1]["task_id"]


def test_does_not_process_outcome_when_artifact_is_not_acknowledged():
    client = FakeLearningClient({"ok": False, "error": "Memory unavailable"})

    with pytest.raises(RuntimeError, match="outcome artifact"):
        submit_outcome(VALID, client)

    assert client.artifacts
    assert client.outcomes == []
    assert client.calls == [("artifact", "run-123")]


def test_reports_failed_learning_outcome_delivery_for_retry():
    client = FakeLearningClient(outcome_response=False)

    with pytest.raises(RuntimeError, match="outcome processing"):
        submit_outcome(VALID, client)

    assert client.artifacts
    assert len(client.outcomes) == 1


def test_real_learning_client_contract_reports_only_learning_level_ack(monkeypatch):
    client = LearningClient(service_url="http://127.0.0.1:1")
    sent = []

    def fake_send_request(request):
        sent.append(request)
        return {"ok": True, "duplicate": True}

    monkeypatch.setattr(client, "_send_request", fake_send_request)

    acknowledged = client.process_outcome(
        task_id="task-123",
        task_type="code-review",
        model="sonnet",
        rating=4,
        tokens=123,
        cost=0.01,
        request_id="run-123",
    )

    assert acknowledged is True
    assert sent[0]["op"] == "process_outcome"
    # LearningClient exposes only a boolean; that is not proof of Thompson's
    # downstream checkpoint/persistence status.
    assert set(sent[0]) >= {"task_id", "request_id", "rating"}
