"""GA-Evolvable Capability Scoring Function.

Generates model capability scores from file characteristics without API calls.
The GA will optimize these weights and thresholds against the RH file corpus.

Inputs: task type, file size, domain markers (CPSEARCH, Solr, etc.)
Outputs: Base score + CI bounds (to be populated with synthetic samples)

Models evaluated:
- Haiku: Fast, good for simple tasks, lower on complex architecture
- Sonnet: Balanced, medium on all tasks
- Opus: Strongest reasoning, better on complex/security tasks

Note: Gemini support was removed as it is not available through Claude Code
or any other implemented provider. See providers/README.md.
"""

from __future__ import annotations

import dataclasses
import math
from pathlib import Path
from typing import Any


# GA-Tunable Parameters (will be evolved)
# ------------------------------------

@dataclasses.dataclass
class ScoringWeights:
    """Weights for the scoring function (GA will tune these)."""

    # Task complexity multipliers
    complexity_haiku_penalty: float = 0.15  # Haiku loses points on complex tasks
    complexity_sonnet_neutral: float = 0.0  # Sonnet unaffected
    complexity_opus_bonus: float = 0.1      # Opus gains on complex

    # Domain expertise (markers like CPSEARCH, Solr, etc.)
    domain_knowledge_weight: float = 0.12
    domain_haiku_penalty: float = 0.10
    domain_sonnet_neutral: float = 0.0
    domain_opus_bonus: float = 0.12

    # File size effect (larger files harder)
    filesize_curve: float = 1.2  # Controls curvature (exponential vs linear)
    filesize_haiku_threshold: int = 500  # Lines where Haiku drops off
    filesize_sonnet_threshold: int = 1500
    filesize_opus_threshold: int = 3000

    # Task-specific base scores
    base_scores: dict[str, dict[str, float]] = dataclasses.field(default_factory=lambda: {
        # Task type -> model -> base score
        "code_review": {"haiku": 0.60, "sonnet": 0.78, "opus": 0.85},
        "deployment": {"haiku": 0.55, "sonnet": 0.75, "opus": 0.82},
        "release_notes": {"haiku": 0.68, "sonnet": 0.72, "opus": 0.74},
        "architecture_design": {"haiku": 0.40, "sonnet": 0.72, "opus": 0.88},
        "security_review": {"haiku": 0.35, "sonnet": 0.70, "opus": 0.90},
        "bug_diagnosis": {"haiku": 0.58, "sonnet": 0.76, "opus": 0.87},
        "alternative_review": {"haiku": 0.50, "sonnet": 0.75, "opus": 0.80},
        "documentation": {"haiku": 0.72, "sonnet": 0.68, "opus": 0.65},
        "testing": {"haiku": 0.65, "sonnet": 0.72, "opus": 0.75},
        "refactoring": {"haiku": 0.58, "sonnet": 0.74, "opus": 0.80},
    })


def _get_default_weights() -> ScoringWeights:
    """Get default weights (GA will replace with evolved ones)."""
    return ScoringWeights()


class ScoringFunction:
    """Generates capability scores from file characteristics."""

    def __init__(self, weights: ScoringWeights | None = None):
        self.weights = weights or _get_default_weights()

    def score(
        self,
        model: str,
        task_type: str,
        file_size_lines: int = 100,
        domain_markers: dict[str, bool] | None = None,
    ) -> float:
        """Generate a score (0-1) for a model-task pair.

        Args:
            model: "haiku", "sonnet", "opus"
            task_type: "code_review", "architecture_design", etc.
            file_size_lines: Number of lines in the file
            domain_markers: {
                "cpsearch": bool,
                "solr": bool,
                "security": bool,
                "deployment": bool,
                "testing": bool,
            }

        Returns:
            Score (0-1), before adding confidence bounds.
        """
        domain_markers = domain_markers or {}

        # 1. Get base score for this model-task pair
        base = self.weights.base_scores.get(task_type, {}).get(model, 0.5)

        # 2. Apply complexity adjustment based on file size
        complexity_penalty = self._complexity_adjustment(model, file_size_lines)
        score = base + complexity_penalty

        # 3. Apply domain knowledge bonus/penalty
        domain_adj = self._domain_adjustment(model, domain_markers)
        score += domain_adj

        # 4. Clamp to [0, 1]
        return max(0.0, min(1.0, score))

    def _complexity_adjustment(self, model: str, file_size_lines: int) -> float:
        """Adjust score based on file complexity (size is proxy)."""
        if model == "haiku":
            threshold = self.weights.filesize_haiku_threshold
            penalty_per_threshold = self.weights.complexity_haiku_penalty
        elif model == "sonnet":
            threshold = self.weights.filesize_sonnet_threshold
            penalty_per_threshold = self.weights.complexity_sonnet_neutral
        elif model == "opus":
            threshold = self.weights.filesize_opus_threshold
            penalty_per_threshold = -self.weights.complexity_opus_bonus  # Negative = bonus
        else:
            return 0.0

        if file_size_lines <= threshold:
            return 0.0

        # Exponential penalty above threshold
        ratio = file_size_lines / threshold
        exponent = self.weights.filesize_curve
        adjustment = penalty_per_threshold * (math.pow(ratio, exponent) - 1.0)
        return adjustment

    def _domain_adjustment(self, model: str, domain_markers: dict[str, bool]) -> float:
        """Adjust score based on domain expertise markers."""
        has_domain = any(domain_markers.values())
        if not has_domain:
            return 0.0

        domain_weight = self.weights.domain_knowledge_weight
        if model == "haiku":
            penalty = self.weights.domain_haiku_penalty * domain_weight
            return -penalty
        elif model == "sonnet":
            return self.weights.domain_sonnet_neutral
        elif model == "opus":
            bonus = self.weights.domain_opus_bonus * domain_weight
            return bonus
        else:
            return 0.0


# Bayesian Confidence Interval Calculation
# -----------------------------------------

def calculate_confidence_interval(
    score: float,
    samples: int,
    confidence_level: float = 0.95,
) -> tuple[float, float]:
    """Calculate Bayesian credible interval for a score.

    Uses Beta-binomial model treating score as a posterior mean.
    With N samples, creates tighter bounds.

    Args:
        score: Base score (0-1)
        samples: Number of evaluation samples
        confidence_level: Credible interval (default 95%)

    Returns:
        (lower_bound, upper_bound)
    """
    # Treat score as posterior mean of Beta distribution
    # With uniform prior, mean = successes / (successes + failures)
    successes = int(round(score * samples))
    failures = samples - successes

    # Add pseudo-counts for regularization (weak prior)
    alpha = successes + 1
    beta = failures + 1

    # Calculate credible interval using percentile method
    # For practical purposes, use simple approximation
    mean = alpha / (alpha + beta)
    variance = (alpha * beta) / ((alpha + beta) ** 2 * (alpha + beta + 1))
    std_dev = math.sqrt(variance)

    # Z-score for 95% CI = 1.96
    z = 1.96 if confidence_level == 0.95 else 1.645  # 90%

    lower = max(0.0, mean - z * std_dev)
    upper = min(1.0, mean + z * std_dev)

    return (lower, upper)


# Task Classification (for determining task type from file)
# ---------

RH_TASK_MARKERS: dict[str, list[str]] = {
    "code_review": [
        "code_review", "CR", "review",
        "bug", "defect", "issue",
    ],
    "deployment": [
        "deploy", "rollout", "release",
        "aws", "openshift", "k8s",
    ],
    "release_notes": [
        "release", "version", "changelog",
        "announcement", "update",
    ],
    "architecture_design": [
        "architecture", "design", "system",
        "component", "interface", "api",
    ],
    "security_review": [
        "security", "vulnerability", "cve",
        "auth", "crypto", "permissions",
    ],
    "bug_diagnosis": [
        "bug", "error", "crash",
        "trace", "stack", "exception",
    ],
    "alternative_review": [
        "alternative", "option", "compare",
        "vs", "trade-off", "consensus",
    ],
    "testing": [
        "test", "unit", "integration",
        "benchmark", "performance",
    ],
    "documentation": [
        "doc", "guide", "readme",
        "tutorial", "comment",
    ],
    "refactoring": [
        "refactor", "cleanup", "simplify",
        "technical_debt", "rewrite",
    ],
}

RH_DOMAIN_MARKERS: dict[str, list[str]] = {
    "cpsearch": ["cpsearch", "solr", "search", "lucene"],
    "deployment": ["aws", "openshift", "k8s", "deployment"],
    "security": ["security", "auth", "crypto", "permissions", "cve"],
    "testing": ["test", "pytest", "junit", "benchmark"],
    "release": ["release", "version", "tag", "changelog"],
}


def detect_task_type(content: str) -> str | None:
    """Detect task type from file content."""
    content_lower = content.lower()
    for task_type, markers in RH_TASK_MARKERS.items():
        if any(marker in content_lower for marker in markers):
            return task_type
    return None


def detect_domain_markers(content: str) -> dict[str, bool]:
    """Detect domain-specific markers in content."""
    content_lower = content.lower()
    return {
        domain: any(marker in content_lower for marker in markers)
        for domain, markers in RH_DOMAIN_MARKERS.items()
    }


# Export for GA
# ----

__all__ = [
    "ScoringFunction",
    "ScoringWeights",
    "calculate_confidence_interval",
    "detect_task_type",
    "detect_domain_markers",
]
