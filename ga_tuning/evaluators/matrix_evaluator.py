#!/usr/bin/env python3
"""
Capability Matrix GA Evaluator

Evaluates scoring weights for capability matrix:
- domain_weight (0.1-0.5): Weight for domain expertise
- complexity_weight (0.2-0.6): Weight for task complexity
- task_weight (0.1-0.5): Weight for task type

Fitness = routing_accuracy (target: 92%+)

Routes 100+ RH files and measures if recommended model matches expected complexity.
Uses heuristic complexity scoring from file content.
"""

import logging
import os
import numpy as np
from pathlib import Path
from typing import Dict, List, Tuple, Optional
import random

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class MatrixEvaluator:
    """Evaluate capability matrix scoring weights"""

    # Model capabilities matrix (domain x task_type)
    CAPABILITY_MATRIX = {
        'haiku-4-5': {
            'code_reading': 0.85,
            'documentation': 0.90,
            'simple_fixes': 0.80,
            'code_review': 0.65,
            'architecture': 0.50,
            'debugging': 0.70,
        },
        'sonnet-5': {
            'code_reading': 0.92,
            'documentation': 0.85,
            'simple_fixes': 0.88,
            'code_review': 0.92,
            'architecture': 0.85,
            'debugging': 0.90,
        },
        'opus-5-5': {
            'code_reading': 0.95,
            'documentation': 0.90,
            'simple_fixes': 0.95,
            'code_review': 0.97,
            'architecture': 0.95,
            'debugging': 0.98,
        },
        'cursor': {
            'code_reading': 0.94,
            'documentation': 0.88,
            'simple_fixes': 0.93,
            'code_review': 0.96,
            'architecture': 0.94,
            'debugging': 0.97,
        },
        'gemini-2.0-flash': {
            'code_reading': 0.90,
            'documentation': 0.87,
            'simple_fixes': 0.85,
            'code_review': 0.88,
            'architecture': 0.92,
            'debugging': 0.84,
        },
    }

    # Optimal model for each task (ground truth)
    OPTIMAL_MODEL = {
        'code_reading': 'sonnet-5',
        'documentation': 'haiku-4-5',
        'simple_fixes': 'sonnet-5',
        'code_review': 'opus-5-5',
        'architecture': 'opus-5-5',
        'debugging': 'opus-5-5',
    }

    # Known task difficulty for the labelled synthetic benchmark. Values near
    # decision boundaries let the optimizer distinguish weight configurations.
    SYNTHETIC_COMPLEXITY = {
        "code_reading": 0.45,
        "documentation": 0.15,
        "simple_fixes": 0.25,
        "code_review": 0.72,
        "architecture": 0.90,
        "debugging": 0.72,
    }

    def __init__(self, rh_memory_dir: Path):
        # Markdown memory files are not labelled routing examples and cannot provide
        # ground truth for routing accuracy. Use an explicit labelled synthetic
        # benchmark until a reviewed, labelled task corpus is supplied.
        self.rh_memory_dir = Path(rh_memory_dir)
        self.evaluation_source = "synthetic-labeled-fixtures"
        self.test_tasks = self._create_synthetic_tasks()
        logger.info("Using %d labelled synthetic routing tasks; no operational routing claim is implied", len(self.test_tasks))

    def _load_rh_files(self) -> List[Tuple[str, str]]:
        """Load RH files for testing"""
        files = []

        if not self.rh_memory_dir.exists():
            logger.warning(f"Memory directory not found: {self.rh_memory_dir}")
            return files

        for md_file in self.rh_memory_dir.glob('*.md'):
            try:
                with open(md_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                    if len(content) > 200:
                        files.append((md_file.name, content))
            except Exception as e:
                logger.error(f"Error loading {md_file}: {e}")

        # If no RH files, create synthetic test cases
        if not files:
            files = self._create_synthetic_tasks()

        logger.info(f"Loaded {len(files)} test tasks")
        return files

    def _create_synthetic_tasks(self) -> List[Tuple[str, str]]:
        """Create synthetic test tasks for evaluation"""
        task_types = ['code_reading', 'documentation', 'simple_fixes', 'code_review', 'architecture', 'debugging']

        tasks = []
        descriptions = {
            "code_reading": "Read and analyze source code to understand control flow and data structures.",
            "documentation": "Write documentation and explain the usage guide and README.",
            "simple_fixes": "Fix a small bug and correct the reported issue with a simple change.",
            "code_review": "Review the code, check correctness, validate behavior, and test edge cases.",
            "architecture": "Design the system architecture and evaluate component boundaries and tradeoffs.",
            "debugging": "Debug the failure, trace the error, and identify the root cause.",
        }
        for i, task_type in enumerate(task_types * 20):  # 120 labelled tasks
            # Keep the label separate from the text classifier so benchmark ground
            # truth is stable even if keyword heuristics change.
            content = descriptions[task_type] + "\n" + "Implementation context " * (20 + i % 50)
            tasks.append((f"synthetic-{task_type}__task_{i}", content))

        return tasks

    def _estimate_complexity(self, content: str) -> float:
        """
        Estimate task complexity from content (0-1).

        Heuristics:
        - Longer content = more complex
        - Keywords like 'architecture', 'design', 'bug' = complex
        - Keywords like 'document', 'write', 'list' = simple
        """
        # Length-based complexity
        length_complexity = min(1.0, len(content) / 2000)

        # Keyword-based complexity
        complex_keywords = ['architecture', 'design', 'algorithm', 'performance', 'bug', 'critical']
        simple_keywords = ['documentation', 'list', 'write', 'format', 'simple']

        content_lower = content.lower()
        complex_count = sum(1 for kw in complex_keywords if kw in content_lower)
        simple_count = sum(1 for kw in simple_keywords if kw in content_lower)

        keyword_complexity = (complex_count - simple_count) / 10.0
        keyword_complexity = np.clip(keyword_complexity, 0, 1)

        # Combined complexity
        complexity = 0.6 * length_complexity + 0.4 * keyword_complexity
        return np.clip(complexity, 0, 1)

    def _estimate_task_type(self, content: str) -> str:
        """Estimate task type from content"""
        content_lower = content.lower()

        type_keywords = {
            'code_review': ['review', 'check', 'validate', 'test'],
            'documentation': ['document', 'readme', 'guide', 'explain'],
            'simple_fixes': ['fix', 'bug', 'issue', 'error'],
            'code_reading': ['read', 'understand', 'analyze'],
            'architecture': ['architecture', 'design', 'system'],
            'debugging': ['debug', 'trace', 'error', 'fail'],
        }

        type_scores = {}
        for task_type, keywords in type_keywords.items():
            score = sum(1 for kw in keywords if kw in content_lower)
            type_scores[task_type] = score

        best_type = max(type_scores, key=type_scores.get)
        return best_type if type_scores[best_type] > 0 else 'code_reading'

    def _score_model(
        self,
        model: str,
        task_type: str,
        complexity: float,
        domain_weight: float,
        complexity_weight: float,
        task_weight: float,
    ) -> float:
        """
        Score model for this task.

        Uses weighted sum of:
        - Task-specific capability (from matrix)
        - Complexity alignment (simple tasks for cheap models)
        - Domain fit
        """
        # Get capability for task
        task_capability = self.CAPABILITY_MATRIX[model].get(task_type, 0.7)

        # Complexity alignment: cheap models for simple tasks, expensive for complex
        if model == 'haiku-4-5':
            complexity_score = 1.0 - complexity  # Good for simple tasks
        elif model == 'sonnet-5':
            complexity_score = 1.0 - abs(complexity - 0.5)  # Good for medium
        else:  # opus
            complexity_score = complexity  # Good for complex tasks

        # Task fit (domain weight)
        domain_score = task_capability

        # Weighted score
        score = (
            task_weight * task_capability +
            complexity_weight * complexity_score +
            domain_weight * domain_score
        )

        # Normalize weights
        total_weight = task_weight + complexity_weight + domain_weight
        score = score / total_weight if total_weight > 0 else 0.5

        return score

    def _select_model(
        self,
        task_type: str,
        complexity: float,
        domain_weight: float,
        complexity_weight: float,
        task_weight: float,
    ) -> str:
        """Select best model for task"""
        scores = {}

        for model in self.CAPABILITY_MATRIX.keys():
            score = self._score_model(
                model, task_type, complexity,
                domain_weight, complexity_weight, task_weight
            )
            scores[model] = score

        return max(scores, key=scores.get)

    def evaluate(self, parameters: Dict[str, float]) -> float:
        """
        Evaluate capability matrix scoring weights.

        Routes test tasks and calculates accuracy against optimal model.
        """
        domain_weight = parameters['domain_weight']
        complexity_weight = parameters['complexity_weight']
        task_weight = parameters['task_weight']

        # Normalize weights
        total_weight = domain_weight + complexity_weight + task_weight
        if total_weight == 0:
            return 0.0

        domain_weight /= total_weight
        complexity_weight /= total_weight
        task_weight /= total_weight

        correct = 0
        total = 0

        for filename, content in self.test_tasks[:100]:  # Use up to 100 tasks
            if filename.startswith("synthetic-") and "__task_" in filename:
                # Synthetic benchmark labels and difficulty are ground truth, not
                # guesses derived from an arbitrary memory document's length.
                task_type = filename[len("synthetic-"):].split("__task_", 1)[0]
                complexity = self.SYNTHETIC_COMPLEXITY[task_type]
            else:
                complexity = self._estimate_complexity(content)
                task_type = self._estimate_task_type(content)

            # Select model with current weights
            selected = self._select_model(
                task_type, complexity,
                domain_weight, complexity_weight, task_weight
            )

            # Get optimal model (ground truth)
            optimal = self.OPTIMAL_MODEL[task_type]

            # Check if selection matches optimal
            if selected == optimal:
                correct += 1

            total += 1

        # Calculate accuracy
        accuracy = correct / total if total > 0 else 0

        logger.debug(
            f"Matrix: domain={domain_weight:.2f}, complexity={complexity_weight:.2f}, "
            f"task={task_weight:.2f} -> accuracy={accuracy:.2%}"
        )

        return accuracy


if __name__ == '__main__':
    # Test evaluator
    rh_memory_dir = Path(os.environ.get('CE_MEMORY_DIR', Path.home() / 'Development/redhat/scm/gitlab/cee/sfloess/claude-global-skills/memory')).expanduser()
    evaluator = MatrixEvaluator(rh_memory_dir)

    test_params = {
        'domain_weight': 0.3,
        'complexity_weight': 0.4,
        'task_weight': 0.3,
    }

    fitness = evaluator.evaluate(test_params)
    print(f"Test fitness: {fitness:.6f}")
