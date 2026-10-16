#!/usr/bin/env python3
"""Expand capability matrix from 5 entries to 20+ with proper statistics.

Uses synthetic scoring function based on file characteristics.
No API calls - all scores are generated from task/domain markers.

Output: Updated capability_matrix.json with:
- 20+ tasks across 10 task types
- 3 models (Haiku, Sonnet, Opus)
- Multiple samples per entry (N >= 5)
- Proper Bayesian credible intervals (95% CI)
- JSON schema for GA evolution

Note: Gemini removed as it is not supported through Claude Code or any
implemented provider. See providers/README.md for supported models.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

from scoring_function import (
    ScoringFunction,
    ScoringWeights,
    calculate_confidence_interval,
    detect_domain_markers,
    detect_task_type,
)


def generate_synthetic_samples(
    model: str,
    task_type: str,
    num_samples: int = 5,
    file_sizes: list[int] | None = None,
    domain_profiles: list[dict[str, bool]] | None = None,
) -> list[float]:
    """Generate synthetic evaluation samples for a model-task pair.

    Samples vary by file size and domain to simulate real variation.

    Args:
        model: "haiku", "sonnet", "opus", "gemini"
        task_type: Task type from RH_TASK_MARKERS
        num_samples: Number of samples to generate
        file_sizes: File sizes for variation (will generate if None)
        domain_profiles: Domain marker profiles for variation

    Returns:
        List of scores (0-1)
    """
    scoring_fn = ScoringFunction()

    if file_sizes is None:
        # Generate varied file sizes: some small, some large
        file_sizes = [
            100 + i * 400 for i in range(num_samples)
        ]

    if domain_profiles is None:
        # Vary domain markers: some with, some without
        domain_profiles = [
            {"cpsearch": i % 2 == 0, "solr": i % 3 == 0}
            for i in range(num_samples)
        ]

    scores = []
    for file_size, domain_profile in zip(file_sizes, domain_profiles):
        score = scoring_fn.score(
            model=model,
            task_type=task_type,
            file_size_lines=file_size,
            domain_markers=domain_profile,
        )
        # Add small random noise to simulate real variation
        noise = 0.02 * (0.5 - (hash(f"{model}{task_type}{file_size}") % 100) / 100)
        score = max(0.0, min(1.0, score + noise))
        scores.append(score)

    return scores


def build_expanded_matrix(
    num_samples_per_entry: int = 5,
) -> dict[str, dict]:
    """Build expanded capability matrix with 20+ tasks.

    Creates entry for each (model, task_type) pair.

    Args:
        num_samples_per_entry: Samples per entry (default 5)

    Returns:
        Matrix dict matching JSON schema
    """
    models = ["haiku", "sonnet", "opus"]

    # All RH task types (matches scoring_function.py)
    task_types = [
        "code_review",
        "deployment",
        "release_notes",
        "architecture_design",
        "security_review",
        "bug_diagnosis",
        "alternative_review",
        "documentation",
        "testing",
        "refactoring",
    ]

    matrix = {}

    for task_type in task_types:
        for model in models:
            # Generate synthetic samples
            samples = generate_synthetic_samples(
                model=model,
                task_type=task_type,
                num_samples=num_samples_per_entry,
            )

            # Calculate statistics
            mean_score = sum(samples) / len(samples)
            ci_lower, ci_upper = calculate_confidence_interval(
                score=mean_score,
                samples=num_samples_per_entry,
                confidence_level=0.95,
            )

            # Key format: "model:task_type"
            key = f"{model}:{task_type}"
            matrix[key] = {
                "model_name": model,
                "task_type": task_type,
                "score": round(mean_score, 3),
                "ci_lower": round(ci_lower, 3),
                "ci_upper": round(ci_upper, 3),
                "confidence": 0.95,
                "samples": num_samples_per_entry,
                "sample_scores": [round(s, 3) for s in samples],
                "last_updated": datetime.now(timezone.utc).isoformat(),
            }

    return matrix


def build_routing_thresholds() -> dict[str, float]:
    """Define "best" vs "workable" thresholds for routing decisions.

    Used by GA validation framework:
    - "best_model": Top performer (score >= threshold_best)
    - "workable_models": All scoring >= threshold_workable
    """
    return {
        "threshold_best": 0.75,  # >= 75% = "best" for this task
        "threshold_workable": 0.60,  # >= 60% = acceptable/workable
        "min_samples_for_routing": 5,  # Require 5+ samples before routing
        "min_ci_width_for_confidence": 0.25,  # Interval width should be <= 0.25
    }


def save_matrix(
    matrix: dict[str, dict],
    thresholds: dict[str, float],
    output_path: Path,
) -> None:
    """Save expanded matrix to JSON file."""
    output = {
        "schema_version": "2.0",
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "description": "GA-evolvable capability matrix with synthetic scores",
        "statistics": {
            "total_entries": len(matrix),
            "models": ["haiku", "sonnet", "opus"],
            "task_types": [
                "code_review", "deployment", "release_notes",
                "architecture_design", "security_review", "bug_diagnosis",
                "alternative_review", "documentation", "testing", "refactoring",
            ],
            "samples_per_entry": 5,
            "confidence_level": 0.95,
        },
        "routing_thresholds": thresholds,
        "scores": matrix,
    }

    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"Saved expanded matrix: {output_path}")
    print(f"  Total entries: {len(matrix)}")
    print(f"  Models: 3 (Haiku, Sonnet, Opus)")
    print(f"  Task types: 10")
    print(f"  Samples per entry: 5")
    print(f"  Total data points: {len(matrix) * 5} = {len(matrix)} entries × 5 samples")


def main():
    """Generate expanded capability matrix."""
    print("Building expanded capability matrix...")
    print()

    # Generate matrix
    matrix = build_expanded_matrix(num_samples_per_entry=5)
    thresholds = build_routing_thresholds()

    # Save
    output_path = Path(__file__).parent / "capability_matrix.json"
    save_matrix(matrix, thresholds, output_path)

    # Print summary
    print()
    print("Matrix Summary:")
    print("=" * 60)

    scores_by_model = {}
    for key, entry in matrix.items():
        model = entry["model_name"]
        if model not in scores_by_model:
            scores_by_model[model] = []
        scores_by_model[model].append(entry["score"])

    for model in ["haiku", "sonnet", "opus"]:
        scores = scores_by_model.get(model, [])
        if not scores:
            continue
        mean = sum(scores) / len(scores)
        min_score = min(scores)
        max_score = max(scores)
        print(f"\n{model.upper()}:")
        print(f"  Entries: {len(scores)}")
        print(f"  Mean score: {mean:.3f}")
        print(f"  Range: {min_score:.3f} - {max_score:.3f}")

    print()
    print("Routing Thresholds:")
    print(f"  Best model (>= {thresholds['threshold_best']}): Top performer per task")
    print(f"  Workable (>= {thresholds['threshold_workable']}): All acceptable models")
    print()
    print("Next: Use learning/validate_routing.py to test GA routing framework")


if __name__ == "__main__":
    main()
