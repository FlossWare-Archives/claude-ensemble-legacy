"""
Test suite for CostAggregator with sample data and metrics demonstration.

This test file demonstrates:
- Daily, weekly, monthly rollups
- Model-specific cost breakdowns
- Compression savings metrics
- Cache hit rate analysis
- Cost impact analysis
"""

import json
import tempfile
from pathlib import Path
from datetime import datetime, timedelta
import sys

from cost_tracking.aggregator import CostAggregator


def create_sample_log_file(directory: Path | None = None) -> Path:
    """Create an isolated sample cost log file with realistic data."""
    log_dir = directory or (Path(tempfile.gettempdir()) / "claude-ensemble-cost-tracking")
    log_dir.mkdir(parents=True, exist_ok=True)

    temp_file = tempfile.NamedTemporaryFile(
        mode='w',
        suffix='.log',
        delete=False,
        dir=str(log_dir)
    )

    # Generate sample entries for the past 30 days
    base_date = datetime.now() - timedelta(days=30)
    entries = []

    # Haiku entries (cheap, frequent, high cache hit rate)
    for i in range(50):
        date = base_date + timedelta(days=i % 30, hours=i % 24)
        entries.append({
            'timestamp': date.isoformat(),
            'model': 'claude-haiku-4.5',
            'provider': 'anthropic',
            'input_tokens': 2000 + (i % 500),
            'output_tokens': 500 + (i % 200),
            'total_cost_usd': 0.0021 + (i % 100) * 0.00001,
            'worker_id': f'worker-{i % 4}',
            'workflow_id': f'workflow-{i % 6}',
            'cache_hit': i % 3 == 0,  # 33% cache hit rate
            'compression_ratio': 0.92,
            'uncompressed_tokens': 2500 + (i % 500) if i % 5 == 0 else 0
        })

    # Sonnet entries (balanced, medium frequency, lower cache hit)
    for i in range(30):
        date = base_date + timedelta(days=i % 30, hours=i % 24 + 6)
        entries.append({
            'timestamp': date.isoformat(),
            'model': 'claude-sonnet-4.5',
            'provider': 'anthropic',
            'input_tokens': 5000 + (i % 1000),
            'output_tokens': 2000 + (i % 500),
            'total_cost_usd': 0.024 + (i % 100) * 0.0001,
            'worker_id': f'worker-{i % 4}',
            'workflow_id': f'workflow-{i % 6}',
            'cache_hit': i % 4 == 0,  # 25% cache hit rate
            'compression_ratio': 0.88,
            'uncompressed_tokens': 7500 + (i % 1000) if i % 4 == 0 else 0
        })

    # Opus entries (expensive, infrequent, very low cache hit)
    for i in range(15):
        date = base_date + timedelta(days=i % 30, hours=i % 24 + 12)
        entries.append({
            'timestamp': date.isoformat(),
            'model': 'claude-opus-4',
            'provider': 'anthropic',
            'input_tokens': 10000 + (i % 2000),
            'output_tokens': 5000 + (i % 1000),
            'total_cost_usd': 0.225 + (i % 100) * 0.001,
            'worker_id': f'worker-{i % 4}',
            'workflow_id': f'workflow-{i % 6}',
            'cache_hit': i % 7 == 0,  # 14% cache hit rate
            'compression_ratio': 0.85,
            'uncompressed_tokens': 16000 + (i % 2000) if i % 3 == 0 else 0
        })

    # GPT-4o entries (alternative model, medium cost)
    for i in range(20):
        date = base_date + timedelta(days=i % 30, hours=i % 24 + 18)
        entries.append({
            'timestamp': date.isoformat(),
            'model': 'gpt-4o',
            'provider': 'openai',
            'input_tokens': 4000 + (i % 800),
            'output_tokens': 1500 + (i % 400),
            'total_cost_usd': 0.018 + (i % 100) * 0.00005,
            'worker_id': f'worker-{i % 4}',
            'workflow_id': f'workflow-{i % 6}',
            'cache_hit': i % 5 == 0,  # 20% cache hit rate
            'compression_ratio': 0.90,
            'uncompressed_tokens': 5500 + (i % 800) if i % 6 == 0 else 0
        })

    # Write entries to log file
    with open(temp_file.name, 'w') as f:
        for entry in sorted(entries, key=lambda x: x['timestamp']):
            f.write(json.dumps(entry) + '\n')

    return Path(temp_file.name)


def test_daily_summary(tmp_path):
    """Test daily cost summary generation."""
    print("\n" + "="*80)
    print("TEST: Daily Cost Summary")
    print("="*80)

    log_file = create_sample_log_file(tmp_path)
    aggregator = CostAggregator(str(log_file))

    report = aggregator.daily_summary()
    assert report["daily_summaries"]
    assert sum(day["total_calls"] for day in report["daily_summaries"].values()) > 0

    print(f"\nPeriod: {report['period']}")
    print(f"Generated: {report['generated_at']}")
    print(f"Total days with data: {len(report['daily_summaries'])}\n")

    # Show first 5 days
    for date, data in list(report['daily_summaries'].items())[:5]:
        print(f"Date: {date}")
        print(f"  Total Calls: {data['total_calls']}")
        print(f"  Total Tokens: {data['total_input_tokens'] + data['total_output_tokens']:,}")
        print(f"  Total Cost: ${data['total_cost']:.6f}")
        print(f"  Models: {', '.join(data['models'].keys())}")

        for model, model_data in data['models'].items():
            print(f"    {model}: {model_data['calls']} calls, "
                  f"${model_data['total_cost']:.6f}")
        print()

    print(f"✓ Daily summary generated successfully\n")

def test_weekly_summary(tmp_path):
    """Test weekly cost summary generation."""
    print("\n" + "="*80)
    print("TEST: Weekly Cost Summary")
    print("="*80)

    log_file = create_sample_log_file(tmp_path)
    aggregator = CostAggregator(str(log_file))

    report = aggregator.weekly_summary()
    assert report["weekly_summaries"]
    assert sum(week["total_calls"] for week in report["weekly_summaries"].values()) > 0

    print(f"\nPeriod: {report['period']}")
    print(f"Generated: {report['generated_at']}")
    print(f"Total weeks with data: {len(report['weekly_summaries'])}\n")

    for week, data in report['weekly_summaries'].items():
        print(f"Week: {week}")
        print(f"  Total Calls: {data['total_calls']}")
        print(f"  Total Tokens: {data['total_input_tokens'] + data['total_output_tokens']:,}")
        print(f"  Total Cost: ${data['total_cost']:.6f}")

        for model, model_data in data['models'].items():
            print(f"    {model}: {model_data['calls']} calls, "
                  f"${model_data['total_cost']:.6f}, "
                  f"avg ${model_data['avg_cost']:.6f}/call")
        print()

    print(f"✓ Weekly summary generated successfully\n")

def test_monthly_summary(tmp_path):
    """Test monthly cost summary generation."""
    print("\n" + "="*80)
    print("TEST: Monthly Cost Summary")
    print("="*80)

    log_file = create_sample_log_file(tmp_path)
    aggregator = CostAggregator(str(log_file))

    report = aggregator.monthly_summary()
    assert report["monthly_summaries"]
    assert sum(month["total_calls"] for month in report["monthly_summaries"].values()) > 0

    print(f"\nPeriod: {report['period']}")
    print(f"Generated: {report['generated_at']}")
    print(f"Total months with data: {len(report['monthly_summaries'])}\n")

    for month, data in report['monthly_summaries'].items():
        print(f"Month: {month}")
        print(f"  Total Calls: {data['total_calls']}")
        print(f"  Total Tokens: {data['total_input_tokens'] + data['total_output_tokens']:,}")
        print(f"  Total Cost: ${data['total_cost']:.6f}")

        for model, model_data in sorted(
            data['models'].items(),
            key=lambda x: x[1]['total_cost'],
            reverse=True
        ):
            print(f"    {model}: {model_data['calls']} calls, "
                  f"${model_data['total_cost']:.6f}, "
                  f"avg ${model_data['avg_cost']:.6f}/call")
        print()

    print(f"✓ Monthly summary generated successfully\n")

def test_savings_report(tmp_path):
    """Test comprehensive savings report generation."""
    print("\n" + "="*80)
    print("TEST: Savings Report with Compression & Cache Metrics")
    print("="*80)

    log_file = create_sample_log_file(tmp_path)
    aggregator = CostAggregator(str(log_file))

    report = aggregator.savings_report()
    assert report["total_entries_analyzed"] > 0
    assert report["compression_metrics"]["total_tokens"] > 0
    assert report["cost_summary"]["total_actual_cost_usd"] >= 0

    print(f"\nReport Type: {report['report_type']}")
    print(f"Generated: {report['generated_at']}")
    print(f"Entries Analyzed: {report['total_entries_analyzed']}\n")

    # Compression Metrics
    print("COMPRESSION METRICS:")
    print("-" * 40)
    metrics = report['compression_metrics']
    print(f"Total Tokens (actual): {metrics['total_tokens']:,}")
    print(f"Compressed Tokens: {metrics['compressed_tokens']:,}")
    print(f"Tokens Saved by Compression: {metrics['tokens_saved']:,}")
    print(f"Compression Ratio: {metrics['compression_ratio']:.2%}")
    print(f"Cache Hits: {metrics['cache_hits']}")
    print(f"Cache Misses: {metrics['cache_misses']}")
    print(f"Cache Hit Rate: {metrics['cache_hit_rate']:.2%}")
    print(f"Estimated Tokens Saved by Cache: {metrics['estimated_tokens_saved_by_cache']:,}")
    print()

    # Cost Summary
    print("COST SUMMARY:")
    print("-" * 40)
    costs = report['cost_summary']
    print(f"Actual Total Cost: ${costs['total_actual_cost_usd']:.6f}")
    print(f"Est. Uncompressed Cost: ${costs['estimated_uncompressed_cost_usd']:.6f}")
    print(f"Compression Savings: ${costs['compression_savings_usd']:.6f}")
    print(f"Cache Hit Savings: ${costs['cache_hit_savings_usd']:.6f}")
    print(f"Total Savings: ${costs['total_savings_usd']:.6f}")
    print(f"Savings Percentage: {costs['savings_percentage']:.2f}%")
    print()

    # Model Breakdown
    print("MODEL COST BREAKDOWN:")
    print("-" * 40)
    for model in sorted(
        report['model_breakdown'].keys(),
        key=lambda x: report['model_breakdown'][x]['total_cost_usd'],
        reverse=True
    ):
        model_data = report['model_breakdown'][model]
        print(f"{model}:")
        print(f"  Total Cost: ${model_data['total_cost_usd']:.6f}")
        print(f"  Calls: {model_data['calls']}")
        print(f"  Cache Hit Rate: {model_data['cache_hit_rate']:.2%}")
        print(f"  Input Tokens: {model_data['input_tokens']:,}")
        print(f"  Output Tokens: {model_data['output_tokens']:,}")
        print()

    print(f"✓ Savings report generated successfully\n")

def test_summary_stats(tmp_path):
    """Test overall summary statistics."""
    print("\n" + "="*80)
    print("TEST: Summary Statistics")
    print("="*80)

    log_file = create_sample_log_file(tmp_path)
    aggregator = CostAggregator(str(log_file))

    stats = aggregator.get_summary_stats()
    assert stats["total_entries"] > 0
    assert stats["total_cost_usd"] >= 0

    print(f"\nTotal Entries: {stats['total_entries']}")
    print(f"Total Cost: ${stats['total_cost_usd']:.6f}")
    print(f"Total Input Tokens: {stats['total_input_tokens']:,}")
    print(f"Total Output Tokens: {stats['total_output_tokens']:,}")
    print(f"Models Used: {', '.join(stats['models_used'])}")
    print(f"Date Range: {stats['date_range']['start']} to {stats['date_range']['end']}")

    print(f"\n✓ Summary statistics generated successfully\n")

def test_add_entry(tmp_path):
    """Test adding new entries to the aggregator."""
    print("\n" + "="*80)
    print("TEST: Add New Entry")
    print("="*80)

    log_file = create_sample_log_file(tmp_path)
    aggregator = CostAggregator(str(log_file))

    initial_count = len(aggregator.entries)
    print(f"\nInitial entries: {initial_count}")

    # Add a new entry
    new_entry = {
        'timestamp': datetime.now().isoformat(),
        'model': 'claude-haiku-4.5',
        'provider': 'anthropic',
        'input_tokens': 3000,
        'output_tokens': 1000,
        'total_cost_usd': 0.0031,
        'worker_id': 'test-worker',
        'cache_hit': True,
        'compression_ratio': 0.93,
        'uncompressed_tokens': 4400
    }

    aggregator.add_entry(new_entry)
    final_count = len(aggregator.entries)
    assert final_count == initial_count + 1
    added = aggregator.entries[-1]
    assert added.model == new_entry["model"]
    assert added.worker_id == "test-worker"
    assert added.cache_hit is True
    assert added.compression_ratio == 0.93
    assert added.uncompressed_tokens == 4400

    print(f"After adding entry: {final_count}")
    print(f"Entry added successfully: {final_count == initial_count + 1}")

    # Verify the entry was persisted
    aggregator2 = CostAggregator(str(log_file))
    assert len(aggregator2.entries) == final_count
    assert aggregator2.entries[-1].worker_id == "test-worker"
    print(f"Verification (reload from disk): {len(aggregator2.entries) == final_count}")

    print(f"\n✓ Add entry test passed\n")


def generate_sample_report():
    """Generate a complete sample report and save as JSON."""
    print("\n" + "="*80)
    print("GENERATING SAMPLE REPORT")
    print("="*80)

    log_file = create_sample_log_file()
    aggregator = CostAggregator(str(log_file))

    # Generate all reports
    daily = aggregator.daily_summary()
    weekly = aggregator.weekly_summary()
    monthly = aggregator.monthly_summary()
    savings = aggregator.savings_report()
    stats = aggregator.get_summary_stats()

    # Combine into comprehensive report
    full_report = {
        'metadata': {
            'generated_at': datetime.now().isoformat(),
            'aggregator_version': '1.0',
            'total_entries_analyzed': len(aggregator.entries)
        },
        'summary': stats,
        'daily_summaries': daily,
        'weekly_summaries': weekly,
        'monthly_summaries': monthly,
        'savings_analysis': savings
    }

    # Save to file
    report_path = Path.home() / '.claude' / 'cost_tracking' / 'sample_report.json'
    report_path.parent.mkdir(parents=True, exist_ok=True)

    with open(report_path, 'w') as f:
        json.dump(full_report, f, indent=2)

    print(f"\nSample report saved to: {report_path}")
    print(f"Report size: {report_path.stat().st_size:,} bytes")

    # Print summary
    print("\nSample Report Summary:")
    print(f"  Total entries: {stats['total_entries']}")
    print(f"  Total cost: ${stats['total_cost_usd']:.6f}")
    print(f"  Compression savings: ${savings['cost_summary']['compression_savings_usd']:.6f}")
    print(f"  Cache hit savings: ${savings['cost_summary']['cache_hit_savings_usd']:.6f}")
    print(f"  Total savings: ${savings['cost_summary']['total_savings_usd']:.6f}")
    print(f"  Savings rate: {savings['cost_summary']['savings_percentage']:.2f}%")

    print(f"\n✓ Sample report generated\n")

    return report_path


if __name__ == '__main__':
    print("\n" + "="*80)
    print("COST AGGREGATOR TEST SUITE")
    print("="*80)

    try:
        # Run all tests
        test_daily_summary()
        test_weekly_summary()
        test_monthly_summary()
        test_savings_report()
        test_summary_stats()
        test_add_entry()
        report_path = generate_sample_report()

        print("\n" + "="*80)
        print("ALL TESTS PASSED ✓")
        print("="*80)
        print(f"\nSample report: {report_path}")

    except Exception as e:
        print(f"\n✗ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
