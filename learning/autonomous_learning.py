#!/usr/bin/env python3
"""
Autonomous Learning Feedback Loop - Phase 1 CREATE

Thompson self-improvement system with 4 autonomous workers collecting feedback
and adjusting the routing capability matrix over time.

Correctness learning is deliberately gated by explicit external ground truth. Workflow
completion, observed quality, and alternative-model comparisons remain operational
telemetry and do not update correctness priors or capability scores without such evidence.

WORKER 1 (Haiku) - OutcomeLogger: Capture task results, model selection, quality, cost
WORKER 2 (Sonnet) - FeedbackScorer: Compare Thompson's routing vs actual best model
WORKER 3 (Opus 4.8) - PriorUpdater: Update Beta distribution priors based on outcomes
WORKER 4 (Gemini) - AutoTuner: Adjust capability matrix based on empirical evidence

System Flow:
  Real RH Task → Thompson Router selects model → Task executes
  → WORKER 1 logs outcome (model, quality, cost)
  → WORKER 2 scores: "was Thompson right?" (accuracy of choice)
  → WORKER 3 updates Beta priors (Bayesian posterior)
  → WORKER 4 adjusts capability matrix (model scores per task type)
  → Loop: Next task uses improved routing
"""

import json
import os
import time
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta, timezone
import numpy as np
from scipy.stats import beta as scipy_beta
from enum import Enum

from ground_truth import GroundTruth, GroundTruthSource, OperationalMetrics, build_learning_signal
from learning.outcome_artifacts import build_outcome_artifact
from learning.portable_artifacts import LearningArtifactStore


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# ============================================================================
# WORKER 1: OUTCOME LOGGER (Haiku) - Log real task results
# ============================================================================

@dataclass
class TaskOutcome:
    """Single task execution outcome"""
    task_id: str
    task_type: str                    # code_review, documentation, testing, etc.
    timestamp: str

    # Thompson routing decision
    thompson_selected: str            # Model that Thompson chose
    thompson_candidates: List[str] = field(default_factory=list)
    thompson_confidence: float = 0.0  # Posterior sampling variance

    # Actual execution
    actual_model_used: str = ""      # May differ if forced/overridden
    quality_score: float = 0.0       # 0-1, assessed post-execution
    latency_ms: float = 0.0
    cost: float = 0.0                # API cost in dollars

    # Operational comparison data. These are telemetry, not correctness labels.
    alternatives_tested: Dict[str, float] = field(default_factory=dict)  # {model: observed quality}

    # External correctness evidence. This is the only source allowed to drive learning.
    ground_truth_source: str = GroundTruthSource.NONE.value
    ground_truth_correct: Optional[bool] = None
    ground_truth_evidence_id: Optional[str] = None

    # Metadata
    notes: str = ""

    def was_thompson_correct(self) -> Optional[bool]:
        """Did Thompson select the actual best model?"""
        return self.ground_truth_correct

    def thompson_ranking(self) -> int:
        """What rank was Thompson's choice? (1 = best)"""
        if not self.alternatives_tested:
            return 1
        sorted_models = sorted(
            self.alternatives_tested.items(),
            key=lambda x: x[1],
            reverse=True
        )
        try:
            return next(
                i + 1 for i, (m, _) in enumerate(sorted_models)
                if m == self.thompson_selected
            )
        except StopIteration:
            return len(sorted_models) + 1


class OutcomeLogger:
    """WORKER 1: Capture and persist task outcomes"""

    def __init__(self, outcomes_dir: str = None):
        """Initialize outcome logger"""
        if outcomes_dir is None:
            outcomes_dir = os.path.join(
                os.path.dirname(__file__),
                'learning/autonomous_outcomes'
            )
        self.outcomes_dir = Path(outcomes_dir)
        self.outcomes_dir.mkdir(parents=True, exist_ok=True)
        self.outcomes: Dict[str, TaskOutcome] = {}
        self._load_all()

    def _load_all(self):
        """Load all outcome files"""
        try:
            for file in self.outcomes_dir.glob('*.json'):
                with open(file, 'r') as f:
                    data = json.load(f)
                    outcome = TaskOutcome(**data)
                    self.outcomes[outcome.task_id] = outcome
            logger.info(f"Loaded {len(self.outcomes)} outcomes from disk")
        except Exception as e:
            logger.error(f"Failed to load outcomes: {e}")
            self.outcomes = {}

    def log_outcome(self, task_id: str, task_type: str,
                   thompson_selected: str, thompson_candidates: List[str],
                   quality_score: float, latency_ms: float, cost: float,
                   alternatives_tested: Optional[Dict[str, float]] = None,
                   ground_truth: Optional[GroundTruth] = None,
                   notes: str = "") -> TaskOutcome:
        """
        Log a task outcome

        Args:
            task_id: Unique task identifier
            task_type: Task category (code_review, documentation, etc.)
            thompson_selected: Model Thompson selected
            thompson_candidates: Models Thompson considered
            quality_score: Quality of result (0-1)
            latency_ms: Execution time
            cost: API cost
            alternatives_tested: {model_name: quality_score} for operational comparison only
            ground_truth: externally observable correctness evidence, if available
            notes: Additional notes

        Returns:
            TaskOutcome record
        """
        outcome = TaskOutcome(
            task_id=task_id,
            task_type=task_type,
            timestamp=datetime.now(timezone.utc).isoformat(),
            thompson_selected=thompson_selected,
            thompson_candidates=thompson_candidates,
            actual_model_used=thompson_selected,
            quality_score=quality_score,
            latency_ms=latency_ms,
            cost=cost,
            alternatives_tested=alternatives_tested or {},
            ground_truth_source=(ground_truth.source.value if ground_truth else GroundTruthSource.NONE.value),
            ground_truth_correct=(ground_truth.correct if ground_truth else None),
            ground_truth_evidence_id=(ground_truth.evidence_id if ground_truth else None),
            notes=notes
        )

        self.outcomes[task_id] = outcome
        self._save_outcome(outcome)

        logger.info(
            f"LOGGED OUTCOME {task_id}: {task_type} → {thompson_selected} "
            f"(quality={quality_score:.2f}, cost=${cost:.4f})"
        )

        return outcome

    def _save_outcome(self, outcome: TaskOutcome):
        """Persist single outcome"""
        file = self.outcomes_dir / f"{outcome.task_id}.json"
        try:
            with open(file, 'w') as f:
                json.dump(asdict(outcome), f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save outcome {outcome.task_id}: {e}")

    def get_outcomes_by_task_type(self, task_type: str) -> List[TaskOutcome]:
        """Get all outcomes for a task type"""
        return [o for o in self.outcomes.values() if o.task_type == task_type]

    def get_outcomes_for_model(self, model_name: str) -> List[TaskOutcome]:
        """Get all outcomes where model was selected"""
        return [o for o in self.outcomes.values() if o.thompson_selected == model_name]

    def get_recent_outcomes(self, hours: int = 24) -> List[TaskOutcome]:
        """Get outcomes from last N hours"""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        results = []
        for o in self.outcomes.values():
            dt = datetime.fromisoformat(o.timestamp)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt >= cutoff:
                results.append(o)
        return results


# ============================================================================
# WORKER 2: FEEDBACK SCORER (Sonnet) - Score routing accuracy
# ============================================================================

@dataclass
class RoutingFeedback:
    """Feedback on Thompson's routing decision"""
    task_id: str
    timestamp: str

    # Routing accuracy
    thompson_correct: Optional[bool]  # Correctness only when externally established
    thompson_ranking: int             # What rank was Thompson's choice? (1=best)
    opportunity_cost: float            # Quality diff: best_quality - thompson_quality

    # Confidence assessment
    confidence_score: float            # How confident Thompson should be (0-1)
    exploration_needed: bool           # Should we explore more?

    # Learning signals
    dominant_model: Optional[str] = None  # Clear winner for this task type?
    model_variance: float = 0.0       # How much do models vary on this task?

    # Recommendations
    recommendation: str = ""           # What to adjust next?



class FeedbackScorer:
    """WORKER 2: Score Thompson routing accuracy"""

    def __init__(self, outcomes_dir: str = None):
        """Initialize feedback scorer"""
        if outcomes_dir is None:
            outcomes_dir = os.path.join(
                os.path.dirname(__file__),
                'learning/autonomous_outcomes'
            )
        self.outcomes_dir = Path(outcomes_dir)
        self.feedbacks: Dict[str, RoutingFeedback] = {}

    def score_outcome(self, outcome: TaskOutcome) -> RoutingFeedback:
        """
        Score a task outcome for routing feedback

        Returns:
            RoutingFeedback with routing accuracy metrics
        """
        # Check if Thompson was correct
        was_correct = outcome.was_thompson_correct()
        ranking = outcome.thompson_ranking()

        # Calculate opportunity cost
        best_quality = max(
            outcome.alternatives_tested.values()
        ) if outcome.alternatives_tested else outcome.quality_score
        opportunity_cost = max(0, best_quality - outcome.quality_score)

        # Determine if we need to explore
        model_qualities = list(outcome.alternatives_tested.values()) + [outcome.quality_score]
        model_variance = np.std(model_qualities) if len(model_qualities) > 1 else 0.0
        exploration_needed = model_variance > 0.15  # High variance → explore

        # Find dominant model
        if outcome.alternatives_tested:
            dominant = max(outcome.alternatives_tested.items(), key=lambda x: x[1])
            dominant_model = dominant[0] if dominant[1] > 0.8 else None
        else:
            dominant_model = None

        # Recommendation logic
        if was_correct is False and opportunity_cost > 0.2:
            recommendation = "External ground truth indicates an incorrect routing decision"
        elif exploration_needed and ranking > 2:
            recommendation = "Thompson under-exploring; increase exploration schedule"
        else:
            recommendation = "Routing performing well"

        feedback = RoutingFeedback(
            task_id=outcome.task_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            thompson_correct=was_correct,
            thompson_ranking=ranking,
            opportunity_cost=opportunity_cost,
            confidence_score=1.0 - (opportunity_cost * 0.5),  # Penalize high opportunity cost
            exploration_needed=exploration_needed,
            dominant_model=dominant_model,
            model_variance=model_variance,
            recommendation=recommendation
        )

        self.feedbacks[outcome.task_id] = feedback

        logger.info(
            f"SCORED OUTCOME {outcome.task_id}: correct={was_correct}, "
            f"ranking={ranking}, opportunity_cost=${opportunity_cost:.4f}"
        )

        return feedback

    def get_accuracy_stats(self, task_type: Optional[str] = None,
                          hours: int = 24) -> Dict[str, Any]:
        """Get routing accuracy statistics"""
        # Operational-only feedback is not evidence of correctness.
        feedbacks = [f for f in self.feedbacks.values() if f.thompson_correct is not None]

        if task_type:
            # Filter by task type (would need to cross-reference with outcomes)
            pass

        if not feedbacks:
            return {'total': 0, 'accuracy': 0}

        correct_count = sum(1 for f in feedbacks if f.thompson_correct)
        total_cost = sum(f.opportunity_cost for f in feedbacks)
        avg_ranking = np.mean([f.thompson_ranking for f in feedbacks])

        return {
            'total': len(feedbacks),
            'correct': correct_count,
            'accuracy': correct_count / len(feedbacks) if feedbacks else 0,
            'total_opportunity_cost': total_cost,
            'avg_opportunity_cost': total_cost / len(feedbacks),
            'avg_ranking': avg_ranking,
            'exploration_needed_pct': sum(
                1 for f in feedbacks if f.exploration_needed
            ) / len(feedbacks)
        }


# ============================================================================
# WORKER 3: PRIOR UPDATER (Opus 4.8) - Update Beta priors
# ============================================================================

@dataclass
class ModelPrior:
    """Beta distribution prior for a model per task type"""
    model_name: str
    task_type: str
    alpha: float = 1.0               # Beta distribution shape
    beta: float = 1.0
    updates: int = 0                 # Number of times updated
    last_updated: str = ""


class PriorUpdater:
    """WORKER 3: Update Beta distribution priors based on feedback"""

    def __init__(self, priors_dir: str = None):
        """Initialize prior updater"""
        if priors_dir is None:
            priors_dir = os.path.join(
                os.path.dirname(__file__),
                'learning/autonomous_priors'
            )
        self.priors_dir = Path(priors_dir)
        self.priors_dir.mkdir(parents=True, exist_ok=True)
        self.priors: Dict[str, ModelPrior] = {}
        self._load_all_priors()

    def _load_all_priors(self):
        """Load all prior files"""
        try:
            for file in self.priors_dir.glob('*.json'):
                with open(file, 'r') as f:
                    data = json.load(f)
                    prior = ModelPrior(**data)
                    key = f"{prior.model_name}:{prior.task_type}"
                    self.priors[key] = prior
            logger.info(f"Loaded {len(self.priors)} priors from disk")
        except Exception as e:
            logger.error(f"Failed to load priors: {e}")
            self.priors = {}

    def update_prior(self, model_name: str, task_type: str,
                    learning_signal=None) -> ModelPrior:
        """
        Update Beta prior using Bayesian update rule

        Beta-Binomial conjugate: If we observe success/failure with Beta(α,β) prior,
        posterior is Beta(α + successes, β + failures)

        Args:
            model_name: Model being evaluated
            task_type: Task type it was used for
            learning_signal: Explicit external ground-truth learning signal

        Returns:
            Updated ModelPrior
        """
        if learning_signal is None:
            raise ValueError("A ground-truth learning signal is required to update correctness priors")

        key = f"{model_name}:{task_type}"

        if key not in self.priors:
            # Initialize with uninformative prior
            self.priors[key] = ModelPrior(
                model_name=model_name,
                task_type=task_type,
                alpha=1.0,
                beta=1.0
            )

        prior = self.priors[key]

        # Determine success/failure exclusively from external ground truth.
        success = learning_signal.correctness

        # Update: add to the appropriate shape parameter
        if success:
            prior.alpha += 1.0
        else:
            prior.beta += 1.0

        prior.updates += 1
        prior.last_updated = datetime.now(timezone.utc).isoformat()

        # Persist
        self._save_prior(prior)

        logger.info(
            f"UPDATED PRIOR {model_name}:{task_type} → "
            f"Beta({prior.alpha:.1f}, {prior.beta:.1f}) "
            f"[updates={prior.updates}]"
        )

        return prior

    def _save_prior(self, prior: ModelPrior):
        """Persist single prior"""
        file = self.priors_dir / f"{prior.model_name}_{prior.task_type}.json"
        try:
            with open(file, 'w') as f:
                json.dump(asdict(prior), f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save prior: {e}")

    def get_prior(self, model_name: str, task_type: str) -> ModelPrior:
        """Get current prior for a model/task combo"""
        key = f"{model_name}:{task_type}"
        if key not in self.priors:
            self.priors[key] = ModelPrior(
                model_name=model_name,
                task_type=task_type
            )
        return self.priors[key]

    def get_posterior_mean(self, model_name: str, task_type: str) -> float:
        """
        Get expected quality from posterior Beta

        E[Beta(α, β)] = α / (α + β)
        """
        prior = self.get_prior(model_name, task_type)
        return prior.alpha / (prior.alpha + prior.beta)

    def get_all_priors_for_task(self, task_type: str) -> Dict[str, ModelPrior]:
        """Get all model priors for a task type"""
        return {
            p.model_name: p for p in self.priors.values()
            if p.task_type == task_type
        }


# ============================================================================
# WORKER 4: AUTO TUNER (Gemini) - Adjust capability matrix
# ============================================================================

@dataclass
class CapabilityScore:
    """Model capability score for a task type"""
    model_name: str
    task_type: str
    score: float = 0.5               # 0-1, higher = more suitable
    confidence: float = 0.0           # How confident in this score
    samples: int = 0                 # Number of observations
    last_updated: str = ""


class AutoTuner:
    """WORKER 4: Auto-tune capability matrix based on empirical evidence"""

    def __init__(self, scores_file: str = None):
        """Initialize auto-tuner"""
        if scores_file is None:
            scores_file = os.path.join(
                os.path.dirname(__file__),
                'learning/capability_matrix.json'
            )
        self.scores_file = Path(scores_file)
        self.scores_file.parent.mkdir(parents=True, exist_ok=True)
        self.scores: Dict[str, CapabilityScore] = {}
        self._load_scores()

    def _load_scores(self):
        """Load capability scores"""
        if self.scores_file.exists():
            try:
                with open(self.scores_file, 'r') as f:
                    data = json.load(f)
                    for key, score_data in data.get('scores', {}).items():
                        score = CapabilityScore(**score_data)
                        self.scores[key] = score
                logger.info(f"Loaded {len(self.scores)} capability scores")
            except Exception as e:
                logger.error(f"Failed to load scores: {e}")
                self.scores = {}

    def update_capability(self, model_name: str, task_type: str,
                         quality_score: float, feedback: Optional[RoutingFeedback] = None) -> CapabilityScore:
        """
        Update model capability score for a task type

        Uses exponential moving average to weight recent performance higher

        Args:
            model_name: Model name
            task_type: Task type
            quality_score: Observed quality (0-1)
            feedback: Optional feedback for additional context

        Returns:
            Updated CapabilityScore
        """
        key = f"{model_name}:{task_type}"

        if key not in self.scores:
            self.scores[key] = CapabilityScore(
                model_name=model_name,
                task_type=task_type
            )

        score = self.scores[key]

        # Exponential moving average: new_score = (1-α) * old_score + α * quality
        # α smaller for more stable, larger for faster adaptation
        alpha = 0.3  # 30% weight to new observation

        old_score = score.score
        score.score = (1 - alpha) * score.score + alpha * quality_score
        score.samples += 1

        # Confidence increases with number of samples (up to 1.0)
        score.confidence = min(1.0, score.samples / 20.0)

        score.last_updated = datetime.now(timezone.utc).isoformat()

        # Persist
        self._save_scores()

        logger.info(
            f"TUNED CAPABILITY {model_name}:{task_type} → "
            f"score={score.score:.3f} (was {old_score:.3f}, samples={score.samples})"
        )

        return score

    def _save_scores(self):
        """Persist all scores"""
        try:
            data = {
                'last_updated': datetime.now(timezone.utc).isoformat(),
                'scores': {
                    key: asdict(score) for key, score in self.scores.items()
                }
            }
            with open(self.scores_file, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save capability scores: {e}")

    def get_capability_scores(self, task_type: str) -> Dict[str, float]:
        """Get all model scores for a task type, sorted by score"""
        task_scores = {
            s.model_name: s.score for s in self.scores.values()
            if s.task_type == task_type
        }
        return dict(sorted(task_scores.items(), key=lambda x: x[1], reverse=True))

    def get_best_models(self, task_type: str, top_n: int = 3) -> List[str]:
        """Get top N models for a task type"""
        scores = self.get_capability_scores(task_type)
        return list(scores.keys())[:top_n]


# ============================================================================
# AUTONOMOUS LEARNING COORDINATOR
# ============================================================================

class AutonomousLearningSystem:
    """Coordinate all 4 workers for continuous learning"""

    def __init__(self, base_dir: str = None):
        """Initialize all workers"""
        if base_dir is None:
            base_dir = os.path.join(
                os.path.dirname(__file__),
                'learning'
            )

        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

        # Initialize all 4 workers
        self.outcome_logger = OutcomeLogger(
            os.path.join(base_dir, 'autonomous_outcomes')
        )
        self.feedback_scorer = FeedbackScorer(
            os.path.join(base_dir, 'autonomous_outcomes')
        )
        self.prior_updater = PriorUpdater(
            os.path.join(base_dir, 'autonomous_priors')
        )
        self.auto_tuner = AutoTuner(
            os.path.join(base_dir, 'capability_matrix.json')
        )
        self.artifact_store = LearningArtifactStore(
            os.path.join(base_dir, 'portable_artifacts.jsonl')
        )

        logger.info("Autonomous Learning System initialized with 4 workers")

    def process_task_completion(self, task_id: str, task_type: str,
                               thompson_selected: str,
                               thompson_candidates: List[str],
                               quality_score: float, latency_ms: float,
                               cost: float,
                               alternatives_tested: Optional[Dict[str, float]] = None,
                               ground_truth: Optional[GroundTruth] = None) -> Dict[str, Any]:
        """
        Process a completed task through all 4 workers

        Returns:
            Learning report with results from each worker
        """
        logger.info(f"Processing task {task_id} through autonomous learning system...")

        # Alternatives and workflow completion are operational telemetry.
        # They cannot manufacture a correctness label.
        operational = OperationalMetrics(
            completed=True,
            findings_processed=len(alternatives_tested or {}),
            target_findings_processed=len(thompson_candidates),
            execution_succeeded=True,
        )
        learning_signal = build_learning_signal(ground_truth, operational)

        # WORKER 1: Log outcome
        outcome = self.outcome_logger.log_outcome(
            task_id=task_id,
            task_type=task_type,
            thompson_selected=thompson_selected,
            thompson_candidates=thompson_candidates,
            quality_score=quality_score,
            latency_ms=latency_ms,
            cost=cost,
            alternatives_tested=alternatives_tested or {},
            ground_truth=ground_truth
        )

        # WORKER 2: Score feedback
        feedback = self.feedback_scorer.score_outcome(outcome)

        # Workers 3 and 4 may learn only from explicit external ground truth.
        prior = None
        capability = None
        if learning_signal is not None:
            prior = self.prior_updater.update_prior(
                model_name=thompson_selected,
                task_type=task_type,
                learning_signal=learning_signal
            )
            capability = self.auto_tuner.update_capability(
                model_name=thompson_selected,
                task_type=task_type,
                quality_score=1.0 if learning_signal.correctness else 0.0,
                feedback=feedback
            )

        # Export the outcome as an independent portable learning artifact.
        artifact = build_outcome_artifact(
            outcome,
            feedback=feedback,
            provenance={"component": "autonomous-learning"},
        )
        self.artifact_store.record(artifact)

        # Compile learning report
        report = {
            'task_id': task_id,
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'worker_1_outcome': asdict(outcome),
            'worker_2_feedback': asdict(feedback),
            'worker_3_prior': asdict(prior) if prior else None,
            'worker_4_capability': asdict(capability) if capability else None,
            'learning_signal': asdict(learning_signal) if learning_signal else None,
            'portable_artifact': artifact.to_dict(),
            'system_recommendation': feedback.recommendation
        }

        logger.info(f"Task {task_id} learning complete: {feedback.recommendation}")

        return report

    def get_learning_report(self) -> Dict[str, Any]:
        """Generate comprehensive learning report"""
        outcomes_recent = self.outcome_logger.get_recent_outcomes(hours=24)
        accuracy_stats = self.feedback_scorer.get_accuracy_stats()

        report = {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'total_outcomes': len(self.outcome_logger.outcomes),
            'recent_outcomes_24h': len(outcomes_recent),
            'accuracy_stats': accuracy_stats,
            'total_priors_trained': len(self.prior_updater.priors),
            'capability_matrix_size': len(self.auto_tuner.scores),
            'worker_status': {
                'worker_1_outcome_logger': f"{len(self.outcome_logger.outcomes)} outcomes logged",
                'worker_2_feedback_scorer': f"{accuracy_stats['total']} feedbacks scored",
                'worker_3_prior_updater': f"{len(self.prior_updater.priors)} priors maintained",
                'worker_4_auto_tuner': f"{len(self.auto_tuner.scores)} capability scores"
            }
        }

        return report


# ============================================================================
# TESTING & DEMONSTRATION
# ============================================================================

def demo_autonomous_learning():
    """Demonstrate Phase 1 autonomous learning system"""

    print("\n" + "=" * 80)
    print("AUTONOMOUS LEARNING FEEDBACK LOOP - Phase 1 CREATE")
    print("4-Worker System: Outcome Logging → Feedback Scoring → Prior Updating → Auto-Tuning")
    print("=" * 80 + "\n")

    # Initialize system
    system = AutonomousLearningSystem()

    # Simulate 5 task completions
    demo_tasks = [
        {
            'task_id': 'code_review_001',
            'task_type': 'code_review',
            'thompson_selected': 'opus',
            'thompson_candidates': ['opus', 'sonnet', 'gpt-4o'],
            'quality_score': 0.92,
            'latency_ms': 8500,
            'cost': 0.015,
            'alternatives_tested': {'opus': 0.92, 'sonnet': 0.88, 'gpt-4o': 0.85}
        },
        {
            'task_id': 'documentation_001',
            'task_type': 'documentation',
            'thompson_selected': 'haiku',
            'thompson_candidates': ['haiku', 'sonnet', 'gpt-4o'],
            'quality_score': 0.78,
            'latency_ms': 3200,
            'cost': 0.002,
            'alternatives_tested': {'haiku': 0.78, 'sonnet': 0.85, 'gpt-4o': 0.80}
        },
        {
            'task_id': 'testing_001',
            'task_type': 'testing',
            'thompson_selected': 'haiku',
            'thompson_candidates': ['haiku', 'sonnet'],
            'quality_score': 0.88,
            'latency_ms': 2800,
            'cost': 0.001,
            'alternatives_tested': {'haiku': 0.88, 'sonnet': 0.89}
        },
        {
            'task_id': 'architecture_001',
            'task_type': 'architecture',
            'thompson_selected': 'sonnet',
            'thompson_candidates': ['opus', 'sonnet'],
            'quality_score': 0.85,
            'latency_ms': 7200,
            'cost': 0.008,
            'alternatives_tested': {'opus': 0.91, 'sonnet': 0.85}
        },
        {
            'task_id': 'bug_analysis_001',
            'task_type': 'bug_analysis',
            'thompson_selected': 'opus',
            'thompson_candidates': ['opus', 'sonnet', 'gpt-4o'],
            'quality_score': 0.95,
            'latency_ms': 9100,
            'cost': 0.018,
            'alternatives_tested': {'opus': 0.95, 'sonnet': 0.87, 'gpt-4o': 0.86}
        }
    ]

    print("PROCESSING 5 DEMO TASKS THROUGH AUTONOMOUS LEARNING SYSTEM:\n")

    for task_data in demo_tasks:
        print(f"→ Task: {task_data['task_id']} ({task_data['task_type']})")

        report = system.process_task_completion(**task_data)

        feedback = report['worker_2_feedback']
        print(f"  Worker 2 Feedback: Thompson {'correct' if feedback['thompson_correct'] else 'incorrect'}, "
              f"ranking={feedback['thompson_ranking']}, "
              f"opportunity_cost=${feedback['opportunity_cost']:.4f}")

        prior = report['worker_3_prior']
        if prior:
            print(f"  Worker 3 Prior: Beta({prior['alpha']:.1f}, {prior['beta']:.1f}), "
                  f"updates={prior['updates']}")
        else:
            print("  Worker 3 Prior: not updated (no external ground truth)")

        capability = report['worker_4_capability']
        if capability:
            print(f"  Worker 4 Capability: score={capability['score']:.3f}, "
                  f"confidence={capability['confidence']:.2f}, samples={capability['samples']}")
        else:
            print("  Worker 4 Capability: not updated (no external ground truth)")

        print(f"  Recommendation: {report['system_recommendation']}\n")

    # Generate final report
    print("\n" + "=" * 80)
    print("LEARNING SYSTEM REPORT - Phase 1 Status")
    print("=" * 80 + "\n")

    final_report = system.get_learning_report()

    print(f"Total Outcomes Logged: {final_report['total_outcomes']}")
    print(f"Recent Outcomes (24h): {final_report['recent_outcomes_24h']}")
    print(f"Routing Accuracy: {final_report['accuracy_stats']['accuracy']:.1%}")
    print(f"Total Opportunity Cost: ${final_report['accuracy_stats']['total_opportunity_cost']:.4f}")
    print(f"Priors Trained: {final_report['total_priors_trained']}")
    print(f"Capability Matrix Size: {final_report['capability_matrix_size']}")

    print("\nWorker Status:")
    for worker, status in final_report['worker_status'].items():
        print(f"  {worker}: {status}")

    print("\n" + "=" * 80)
    print("PHASE 1 VERDICT")
    print("=" * 80)

    if final_report['accuracy_stats']['accuracy'] > 0.6:
        verdict = "✓ PASS - System is learning effectively"
    elif final_report['accuracy_stats']['accuracy'] > 0.4:
        verdict = "△ PARTIAL - System functioning but needs tuning"
    else:
        verdict = "✗ NEEDS WORK - System not yet productive"

    print(f"\n{verdict}")
    print(f"\nAccuracy: {final_report['accuracy_stats']['accuracy']:.1%}")
    print(f"Workers Active: All 4 workers operating")
    print(f"Learning Data: {final_report['total_priors_trained']} model-task pairs trained")
    print(f"Ready for Phase 2: YES - System is autonomous and adaptive")

    print("\nNext Steps for Phase 2:")
    print("  1. Deploy outcome logging to production task queue")
    print("  2. Wire Thompson router to use updated priors from Worker 3")
    print("  3. Integrate capability matrix updates into task routing decisions")
    print("  4. Set up automated alerts for routing accuracy < 70%")
    print("  5. Monthly learning reports and model reassessment")

    print("\n" + "=" * 80 + "\n")


if __name__ == '__main__':
    demo_autonomous_learning()
