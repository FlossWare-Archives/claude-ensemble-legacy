"""Tests for portable outcome-feedback artifacts."""
from dataclasses import dataclass

from learning.outcome_artifacts import build_outcome_artifact


@dataclass
class Outcome:
    task_id: str = "task-1"
    task_type: str = "code_review"
    timestamp: str = "2026-10-04T12:00:00+00:00"
    thompson_selected: str = "model-a"
    thompson_candidates: list[str] | None = None
    actual_model_used: str = "model-a"
    quality_score: float = 0.9
    latency_ms: float = 1250
    cost: float = 0.01
    ground_truth_source: str = "human_acceptance"
    ground_truth_correct: bool | None = True
    ground_truth_evidence_id: str | None = "e-1"

    def __post_init__(self):
        if self.thompson_candidates is None:
            self.thompson_candidates = ["model-a", "model-b"]


def test_outcome_becomes_independent_portable_artifact():
    artifact = build_outcome_artifact(
        Outcome(),
        feedback={"thompson_ranking": 1, "opportunity_cost": 0.0},
    )

    restored = type(artifact).from_json(artifact.to_json())

    assert restored.artifact_type == "outcome-feedback"
    assert restored.payload["outcome"]["selected_model"] == "model-a"
    assert restored.payload["outcome"]["candidates"] == ("model-a", "model-b")
    assert restored.payload["feedback"]["thompson_ranking"] == 1
    assert restored.payload["ground_truth"]["correct"] is True
    assert restored.provenance["task_id"] == "task-1"


def test_artifact_allowlists_exported_fields():
    outcome = Outcome()
    artifact = build_outcome_artifact(
        outcome,
        feedback={
            "recommendation": "keep",
            "private_reasoning": "must not export",
            "credentials": "must not export",
        },
    )

    payload = artifact.to_dict()["payload"]

    assert set(payload["outcome"]) == {
        "task_id",
        "task_type",
        "actual_model_used",
        "selected_model",
        "candidates",
        "quality_score",
        "latency_ms",
        "cost",
    }
    assert set(payload["feedback"]) == {"recommendation"}
    assert "private_reasoning" not in payload["feedback"]
    assert "credentials" not in payload["feedback"]
    assert "notes" not in payload["outcome"]


def test_artifact_preserves_unknown_ground_truth_as_unlabeled():
    outcome = Outcome(ground_truth_source="none", ground_truth_correct=None)
    artifact = build_outcome_artifact(outcome)

    assert artifact.payload["ground_truth"]["source"] == "none"
    assert artifact.payload["ground_truth"]["correct"] is None
