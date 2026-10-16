"""Mocked end-to-end contract tests for evidence-gated CE learning.

No service daemon, Claude Code runtime, socket, or external API is required.
"""
from __future__ import annotations

import unittest
from typing import Any

from tools.record_workflow_outcome import submit_outcome


class FakeLearningClient:
    def __init__(self, *, artifact_ok: bool = True, outcome_ok: bool = True) -> None:
        self.artifact_ok = artifact_ok
        self.outcome_ok = outcome_ok
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def record_artifact(self, artifact: dict[str, Any], request_id: str | None = None) -> dict[str, Any]:
        self.calls.append(("record_artifact", {"artifact": artifact, "request_id": request_id}))
        return {"ok": self.artifact_ok, "artifact_status": "stored" if self.artifact_ok else "rejected"}

    def process_outcome(self, **kwargs: Any) -> bool:
        self.calls.append(("process_outcome", kwargs))
        return self.outcome_ok


def valid_outcome() -> dict[str, Any]:
    return {
        "run_id": "run-123",
        "task_id": "task-456",
        "task_type": "code-review",
        "model": "sonnet",
        "rating": 4,
        "tokens": 1200,
        "cost": 0.02,
        "evidence": {
            "kind": "test-report",
            "reference": "ci://run/123",
            "summary": "Regression suite passed and the targeted defect is covered.",
        },
    }


class EvidenceGatedLearningTests(unittest.TestCase):
    def test_artifact_is_acknowledged_before_learning_outcome_is_processed(self) -> None:
        client = FakeLearningClient()
        result = submit_outcome(valid_outcome(), client)

        self.assertTrue(result["ok"])
        self.assertEqual(result["run_id"], "run-123")
        self.assertEqual([name for name, _ in client.calls], ["record_artifact", "process_outcome"])
        artifact_call = client.calls[0][1]
        self.assertEqual(artifact_call["request_id"], "run-123")
        artifact = artifact_call["artifact"]
        self.assertEqual(artifact["artifact_type"], "workflow-outcome")
        self.assertEqual(artifact["payload"]["run_id"], "run-123")
        self.assertEqual(artifact["provenance"]["evidence"]["reference"], "ci://run/123")
        outcome_call = client.calls[1][1]
        self.assertEqual(outcome_call["request_id"], "run-123")
        self.assertEqual(outcome_call["task_id"], "task-456")
        self.assertEqual(outcome_call["rating"], 4)

    def test_artifact_failure_stops_before_learner_update(self) -> None:
        client = FakeLearningClient(artifact_ok=False)
        with self.assertRaisesRegex(RuntimeError, "outcome artifact"):
            submit_outcome(valid_outcome(), client)
        self.assertEqual([name for name, _ in client.calls], ["record_artifact"])

    def test_learning_rejection_is_not_reported_as_success(self) -> None:
        client = FakeLearningClient(outcome_ok=False)
        with self.assertRaisesRegex(RuntimeError, "outcome processing"):
            submit_outcome(valid_outcome(), client)
        self.assertEqual([name for name, _ in client.calls], ["record_artifact", "process_outcome"])

    def test_missing_evidence_and_invalid_ratings_fail_closed(self) -> None:
        for mutate in (
            lambda value: value.pop("evidence"),
            lambda value: value.update(rating=6),
            lambda value: value.update(rating=True),
            lambda value: value["evidence"].update(reference=" "),
        ):
            payload = valid_outcome()
            mutate(payload)
            client = FakeLearningClient()
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                submit_outcome(payload, client)
            self.assertEqual(client.calls, [])


if __name__ == "__main__":
    unittest.main()
