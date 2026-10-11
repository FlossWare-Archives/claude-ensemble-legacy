"""
Test suite for the cost tracking logger.

Simulates 5 RH API calls with different models and verifies logging behavior.
"""

import json
import tempfile
from pathlib import Path

from cost_tracking.logger import CostLogger


def test_cost_calculations():
    """Test cost calculation for different models."""
    logger = CostLogger()

    test_cases = [
        ("haiku", 1000, 500),      # $0.0008 + $0.0012 = $0.002
        ("sonnet", 2000, 1000),    # $0.006 + $0.015 = $0.021
        ("opus", 1500, 2000),      # $0.0225 + $0.09 = $0.1125
        ("gemini", 10000, 5000),   # $0.00075 + $0.0015 = $0.00225
    ]

    for model, input_tokens, output_tokens in test_cases:
        cost = logger.calculate_cost(model, input_tokens, output_tokens)
        print(f"{model:8} | {input_tokens:6} → {output_tokens:6} | "
              f"${cost:.6f}")


def test_logging_5_calls():
    """Simulate 5 RH API calls with different models and verify logging."""

    # Use a temporary file for this test
    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = Path(tmpdir) / "test_costs.jsonl"
        logger = CostLogger(str(log_path))

        # Simulate 5 RH API calls with various models
        calls = [
            {
                "model": "haiku",
                "input_tokens": 2500,
                "output_tokens": 1200,
                "task_name": "code_review",
                "source": "api",
                "metadata": {"pr_id": "1234", "file_count": 5},
            },
            {
                "model": "sonnet",
                "input_tokens": 5000,
                "output_tokens": 3500,
                "task_name": "architecture_design",
                "source": "api",
                "metadata": {"project": "disseminator", "reviewed_components": 3},
            },
            {
                "model": "opus",
                "input_tokens": 8000,
                "output_tokens": 4500,
                "task_name": "security_review",
                "source": "api",
                "metadata": {"security_level": "critical", "findings": 2},
            },
            {
                "model": "haiku",
                "input_tokens": 1500,
                "output_tokens": 800,
                "task_name": "refactoring",
                "source": "cached",
                "metadata": {"cache_hit": True, "module": "cpsearch"},
            },
            {
                "model": "gemini",
                "input_tokens": 10000,
                "output_tokens": 6000,
                "task_name": "alternative_review",
                "source": "api",
                "metadata": {"model_comparison": True, "consensus": "approved"},
            },
        ]

        print("\n" + "=" * 100)
        print("LOGGING 5 RH API CALLS")
        print("=" * 100 + "\n")

        logged_entries = []
        for call in calls:
            entry = logger.log_call(
                model=call["model"],
                input_tokens=call["input_tokens"],
                output_tokens=call["output_tokens"],
                task_name=call["task_name"],
                source=call["source"],
                metadata=call["metadata"],
            )
            logged_entries.append(entry)

            print(f"✓ Logged {call['model']:8} | {call['task_name']:20} | "
                  f"${entry['cost_usd']:.6f}")

        print("\n" + "=" * 100)
        print("AGGREGATE STATISTICS")
        print("=" * 100 + "\n")

        stats = logger.get_stats()

        print(f"Total API Calls:  {stats['total_calls']}")
        print(f"Total Tokens:     {stats['total_tokens']:,}")
        print(f"Total Cost:       ${stats['total_cost_usd']:.6f}\n")

        print("BY MODEL:")
        for model, model_stats in stats["by_model"].items():
            if model_stats["calls"] > 0:
                print(f"  {model:8} | Calls: {model_stats['calls']:2} | "
                      f"Tokens: {model_stats['tokens']:6,} | "
                      f"Cost: ${model_stats['cost']:.6f}")

        print("\nBY TASK:")
        for task, task_stats in sorted(
            stats["by_task"].items(), key=lambda x: x[1]["cost"], reverse=True
        ):
            print(f"  {task:25} | Calls: {task_stats['calls']:2} | "
                  f"Tokens: {task_stats['tokens']:6,} | "
                  f"Cost: ${task_stats['cost']:.6f}")

        print("\n" + "=" * 100)
        print("RAW JSON LOG (append-only jsonlines format)")
        print("=" * 100 + "\n")

        logs = logger.read_logs()
        for i, log_entry in enumerate(logs, 1):
            print(f"Entry {i}:")
            print(json.dumps(log_entry, indent=2))
            print()

        # Verify the log file exists and has correct number of entries
        assert log_path.exists(), "Log file should exist"
        assert len(logs) == 5, f"Expected 5 log entries, got {len(logs)}"
        print("✓ All 5 entries logged successfully")
        print(f"✓ Log file: {log_path}")

        return logs, stats


def test_invalid_model():
    """Test that invalid models raise appropriate errors."""
    logger = CostLogger()

    try:
        logger.calculate_cost("invalid_model", 1000, 500)
        assert False, "Should have raised ValueError"
    except ValueError as e:
        print(f"✓ Correctly rejected invalid model: {e}")


def test_pricing_consistency():
    """Verify pricing calculations match expectations."""
    logger = CostLogger()

    # Haiku: $0.80 input, $2.40 output per 1M tokens
    # 1M input + 1M output = $0.80 + $2.40 = $3.20
    cost = logger.calculate_cost("haiku", 1_000_000, 1_000_000)
    assert cost == 3.20, f"Haiku pricing incorrect: {cost} != 3.20"
    print(f"✓ Haiku pricing: 1M+1M tokens = ${cost:.2f}")

    # Sonnet: $3.00 input, $15.00 output per 1M tokens
    # 1M input + 1M output = $3.00 + $15.00 = $18.00
    cost = logger.calculate_cost("sonnet", 1_000_000, 1_000_000)
    assert cost == 18.00, f"Sonnet pricing incorrect: {cost} != 18.00"
    print(f"✓ Sonnet pricing: 1M+1M tokens = ${cost:.2f}")

    # Opus: $15.00 input, $45.00 output per 1M tokens
    # 1M input + 1M output = $15.00 + $45.00 = $60.00
    cost = logger.calculate_cost("opus", 1_000_000, 1_000_000)
    assert cost == 60.00, f"Opus pricing incorrect: {cost} != 60.00"
    print(f"✓ Opus pricing: 1M+1M tokens = ${cost:.2f}")


if __name__ == "__main__":
    print("\n" + "=" * 100)
    print("COST TRACKER TEST SUITE")
    print("=" * 100)

    print("\n[TEST 1] Pricing Calculations")
    print("-" * 100)
    test_pricing_consistency()

    print("\n[TEST 2] Cost Calculations (Sample)")
    print("-" * 100)
    test_cost_calculations()

    print("\n[TEST 3] Logging 5 RH API Calls")
    print("-" * 100)
    logs, stats = test_logging_5_calls()

    print("\n[TEST 4] Invalid Model Handling")
    print("-" * 100)
    test_invalid_model()

    print("\n" + "=" * 100)
    print("ALL TESTS PASSED")
    print("=" * 100 + "\n")



def test_log_call_accepts_provider_reported_cost(tmp_path):
    logger = CostLogger(tmp_path / "cost.jsonl")
    entry = logger.log_call(
        model="gemini-test",
        input_tokens=10,
        output_tokens=5,
        task_name="test",
        provider="google",
        cost_usd=0.123,
    )
    assert entry["cost_usd"] == 0.123
    assert entry["provider"] == "google"
