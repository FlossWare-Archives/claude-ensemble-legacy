from learning.outcome_experiment import compare_outcomes

import pytest


def test_compare_outcomes_measures_quality_cost_latency_and_regressions():
    baseline = [
        {"task_id": "a", "quality_score": 0.70, "cost": 0.010, "latency_ms": 100},
        {"task_id": "b", "quality_score": 0.80, "cost": 0.020, "latency_ms": 200},
    ]
    learning = [
        {"task_id": "a", "quality_score": 0.85, "cost": 0.008, "latency_ms": 120},
        {"task_id": "b", "quality_score": 0.65, "cost": 0.015, "latency_ms": 180},
    ]

    result = compare_outcomes(baseline, learning)

    assert result.paired_count == 2
    assert result.quality_delta == 0.0
    assert result.cost_delta < 0
    assert result.latency_delta_ms == 0
    assert result.regressions == 1


def test_compare_outcomes_uses_only_paired_workload_records():
    result = compare_outcomes(
        [{"task_id": "a", "quality_score": 0.5}],
        [
            {"task_id": "a", "quality_score": 0.7},
            {"task_id": "b", "quality_score": 0.9},
        ],
    )

    assert result.baseline_count == 1
    assert result.learning_count == 2
    assert result.paired_count == 1
    assert result.quality_delta == pytest.approx(0.2)


def test_compare_outcomes_rejects_duplicate_task_ids():
    with pytest.raises(ValueError, match="duplicate task_id"):
        compare_outcomes(
            [{"task_id": "a"}, {"task_id": "a"}],
            [{"task_id": "a"}],
        )


def test_compare_outcomes_skips_missing_metrics():
    result = compare_outcomes(
        [{"task_id": "a", "quality_score": 0.5}],
        [{"task_id": "a", "quality_score": 0.7, "cost": 0.2}],
    )

    assert result.quality_delta == pytest.approx(0.2)
    assert result.cost_delta == 0.2
    assert result.baseline_latency_ms == 0.0
    assert result.learning_latency_ms == 0.0
