#!/usr/bin/env python3
"""Tests for separation of operational telemetry and correctness signals."""

import tempfile
import unittest

from autonomous_learning import PriorUpdater

from ground_truth import (
    GroundTruth,
    GroundTruthSource,
    OperationalMetrics,
    build_learning_signal,
)


class GroundTruthTests(unittest.TestCase):
    def test_successful_workflow_without_ground_truth_has_no_learning_signal(self):
        operational = OperationalMetrics(
            completed=True,
            findings_processed=10,
            target_findings_processed=10,
            execution_succeeded=True,
        )

        signal = build_learning_signal(None, operational)

        self.assertIsNone(signal)

    def test_successful_workflow_with_incorrect_external_outcome_is_negative(self):
        operational = OperationalMetrics(
            completed=True,
            findings_processed=10,
            target_findings_processed=10,
            execution_succeeded=True,
        )
        ground_truth = GroundTruth(
            source=GroundTruthSource.HUMAN_DISMISSAL,
            correct=False,
            evidence_id="review-comment-123",
        )

        signal = build_learning_signal(ground_truth, operational)

        self.assertIsNotNone(signal)
        self.assertFalse(signal.correctness)

    def test_external_positive_outcome_produces_positive_learning_signal(self):
        operational = OperationalMetrics(completed=True, execution_succeeded=True)
        ground_truth = GroundTruth(
            source=GroundTruthSource.HUMAN_ACCEPTANCE,
            correct=True,
            evidence_id="review-comment-456",
        )

        signal = build_learning_signal(ground_truth, operational)

        self.assertIsNotNone(signal)
        self.assertTrue(signal.correctness)

    def test_operational_metrics_cannot_override_negative_ground_truth(self):
        operational = OperationalMetrics(
            completed=True,
            findings_processed=100,
            target_findings_processed=1,
            execution_succeeded=True,
        )
        ground_truth = GroundTruth(
            source=GroundTruthSource.REVERT,
            correct=False,
            evidence_id="commit-789",
        )

        signal = build_learning_signal(ground_truth, operational)

        self.assertFalse(signal.correctness)

    def test_prior_cannot_update_from_operational_quality_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            updater = PriorUpdater(tmp)
            with self.assertRaises(ValueError):
                updater.update_prior(
                    model_name="model-a",
                    task_type="code_review",
                    learning_signal=None,
                )

    def test_negative_ground_truth_updates_prior_as_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            updater = PriorUpdater(tmp)
            signal = build_learning_signal(
                GroundTruth(
                    source=GroundTruthSource.HUMAN_DISMISSAL,
                    correct=False,
                    evidence_id="review-1",
                ),
                OperationalMetrics(completed=True, execution_succeeded=True),
            )
            prior = updater.update_prior(
                model_name="model-a",
                task_type="code_review",
                learning_signal=signal,
            )
            self.assertEqual(prior.alpha, 1.0)
            self.assertEqual(prior.beta, 2.0)


if __name__ == "__main__":
    unittest.main()
