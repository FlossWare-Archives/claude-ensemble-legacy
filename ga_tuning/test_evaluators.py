#!/usr/bin/env python3
"""
Test script for all 5 GA evaluators

Validates that each evaluator:
1. Loads correctly
2. Produces reasonable fitness scores
3. Respects parameter constraints
4. Handles edge cases
"""

import sys
import os
from pathlib import Path

# Add evaluators to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'evaluators'))

from compression_evaluator import CompressionEvaluator
from thompson_evaluator import ThompsonEvaluator
from caching_evaluator import CachingEvaluator
from matrix_evaluator import MatrixEvaluator
from dashboard_evaluator import DashboardEvaluator


def test_compression():
    """Test compression evaluator"""
    print("\n" + "=" * 70)
    print("TESTING COMPRESSION EVALUATOR")
    print("=" * 70)

    rh_dir = Path(os.environ.get('CE_MEMORY_DIR', Path.home() / 'Development/redhat/scm/gitlab/cee/sfloess/claude-global-skills/memory')).expanduser()
    evaluator = CompressionEvaluator(rh_dir)

    # Test parameters
    test_cases = [
        {'compression_level': 0.0, 'target_reduction': 0.2},  # Minimal compression
        {'compression_level': 2.5, 'target_reduction': 0.4},  # Medium compression
        {'compression_level': 5.0, 'target_reduction': 0.7},  # Maximum compression
    ]

    print(f"Test corpus: {len(evaluator.test_files)} RH memory files")

    for i, params in enumerate(test_cases, 1):
        fitness = evaluator.evaluate(params)
        print(f"Test {i}: compression={params['compression_level']:.1f}, "
              f"target={params['target_reduction']:.1f} -> fitness={fitness:.6f}")

    # Validate: should be in [0, 1]
    assert 0 <= evaluator.evaluate(test_cases[0]) <= 1.0, "Fitness out of bounds"
    print("\nValidation: PASS - Fitness scores within [0, 1]")



def test_compression_uses_synthetic_fixture_without_local_corpus():
    evaluator = CompressionEvaluator(Path("/definitely/missing/claude-ensemble-memory-corpus"))
    assert evaluator.synthetic_corpus is True
    assert len(evaluator.test_files) >= 2
    fitness = evaluator.evaluate({"compression_level": 3.0, "target_reduction": 0.3})
    assert 0.0 < fitness <= 1.0, f"Expected measurable fixture fitness, got {fitness}"

def test_thompson():
    """Test Thompson router evaluator"""
    print("\n" + "=" * 70)
    print("TESTING THOMPSON ROUTER EVALUATOR")
    print("=" * 70)

    evaluator = ThompsonEvaluator()

    # Test parameters
    test_cases = [
        {'alpha_prior': 0.5, 'beta_prior': 0.5, 'cost_weight': 0.1},  # Minimal priors
        {'alpha_prior': 1.5, 'beta_prior': 1.5, 'cost_weight': 0.3},  # Balanced
        {'alpha_prior': 3.0, 'beta_prior': 3.0, 'cost_weight': 0.5},  # Strong priors
    ]

    for i, params in enumerate(test_cases, 1):
        fitness = evaluator.evaluate(params)
        print(f"Test {i}: alpha={params['alpha_prior']:.1f}, "
              f"beta={params['beta_prior']:.1f}, cost_weight={params['cost_weight']:.1f} "
              f"-> fitness={fitness:.6f}")

    # Validate: should be in [0, 1]
    assert 0 <= evaluator.evaluate(test_cases[0]) <= 1.0, "Fitness out of bounds"
    print("\nValidation: PASS - Fitness scores within [0, 1]")


def test_caching():
    """Test caching evaluator"""
    print("\n" + "=" * 70)
    print("TESTING CACHING EVALUATOR")
    print("=" * 70)

    evaluator = CachingEvaluator()

    # Test parameters
    test_cases = [
        {'ttl_seconds': 60.0, 'cache_threshold': 0.1},   # Short TTL, low threshold
        {'ttl_seconds': 300.0, 'cache_threshold': 0.5},  # Medium TTL, medium threshold
        {'ttl_seconds': 600.0, 'cache_threshold': 0.9},  # Long TTL, high threshold
    ]

    for i, params in enumerate(test_cases, 1):
        fitness = evaluator.evaluate(params)
        print(f"Test {i}: ttl={params['ttl_seconds']:.0f}s, "
              f"threshold={params['cache_threshold']:.1f} -> fitness={fitness:.6f}")

    # Validate: should be in [0, 1]
    assert 0 <= evaluator.evaluate(test_cases[0]) <= 1.0, "Fitness out of bounds"
    print("\nValidation: PASS - Fitness scores within [0, 1]")


def test_matrix():
    """Test capability matrix evaluator"""
    print("\n" + "=" * 70)
    print("TESTING CAPABILITY MATRIX EVALUATOR")
    print("=" * 70)

    rh_dir = Path(os.environ.get('CE_MEMORY_DIR', Path.home() / 'Development/redhat/scm/gitlab/cee/sfloess/claude-global-skills/memory')).expanduser()
    evaluator = MatrixEvaluator(rh_dir)

    # Test parameters
    test_cases = [
        {'domain_weight': 0.1, 'complexity_weight': 0.2, 'task_weight': 0.1},  # Low weights
        {'domain_weight': 0.3, 'complexity_weight': 0.4, 'task_weight': 0.3},  # Balanced
        {'domain_weight': 0.5, 'complexity_weight': 0.6, 'task_weight': 0.5},  # High weights
    ]

    print(f"Test corpus: {len(evaluator.test_tasks)} labelled synthetic tasks")

    scores = []
    for i, params in enumerate(test_cases, 1):
        fitness = evaluator.evaluate(params)
        scores.append(fitness)
        print(f"Test {i}: domain={params['domain_weight']:.1f}, "
              f"complexity={params['complexity_weight']:.1f}, task={params['task_weight']:.1f} "
              f"-> fitness={fitness:.6f}")

    # The labelled fixture benchmark must not silently degenerate to zero fitness.
    assert evaluator.evaluation_source == "synthetic-labeled-fixtures"
    assert max(scores) > 0.0, f"Expected at least one correct routing choice, got {scores}"
    assert all(0.0 <= score <= 1.0 for score in scores), "Fitness out of bounds"
    print("\nValidation: PASS - labelled routing fitness is measurable and bounded")


def test_dashboard():
    """Test dashboard evaluator"""
    print("\n" + "=" * 70)
    print("TESTING DASHBOARD EVALUATOR")
    print("=" * 70)

    evaluator = DashboardEvaluator()

    # Test parameters
    test_cases = [
        {'learning_rate': 0.01, 'exploration_decay': 0.85, 'alert_threshold': 0.3},   # Low LR
        {'learning_rate': 0.1, 'exploration_decay': 0.95, 'alert_threshold': 0.6},    # Medium LR
        {'learning_rate': 0.2, 'exploration_decay': 0.99, 'alert_threshold': 0.9},    # High LR
    ]

    for i, params in enumerate(test_cases, 1):
        fitness = evaluator.evaluate(params)
        print(f"Test {i}: lr={params['learning_rate']:.2f}, "
              f"decay={params['exploration_decay']:.2f}, alert={params['alert_threshold']:.1f} "
              f"-> fitness={fitness:.6f}")

    # Validate: should be in [0, 1]
    assert 0 <= evaluator.evaluate(test_cases[0]) <= 1.0, "Fitness out of bounds"
    print("\nValidation: PASS - Fitness scores within [0, 1]")


def main():
    """Run all tests"""
    print("\n" + "=" * 70)
    print("GA EVALUATOR TEST SUITE")
    print("=" * 70)

    try:
        test_compression()
        test_compression_uses_synthetic_fixture_without_local_corpus()
        test_thompson()
        test_caching()
        test_matrix()
        test_dashboard()

        print("\n" + "=" * 70)
        print("ALL TESTS PASSED")
        print("=" * 70)
        return 0

    except AssertionError as e:
        print(f"\nTEST FAILED: {e}")
        return 1
    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(main())
