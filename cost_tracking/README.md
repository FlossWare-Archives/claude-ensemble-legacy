# Cost Tracking Aggregator

A comprehensive cost aggregation module for tracking Claude API usage with daily, weekly, and monthly rollups, featuring compression and cache hit analysis.

## Overview

The Cost Aggregator reads append-only cost logs and generates detailed cost summaries broken down by:
- **Daily rollups**: Per-day cost breakdowns by model
- **Weekly rollups**: ISO week cost summaries with averages
- **Monthly rollups**: Per-month aggregations by model
- **Savings analysis**: Compression savings, cache hit rates, and cost impact

## Quick Start

### Basic Usage

```python
from aggregator import CostAggregator

# Initialize (reads from ~/.claude/cost_tracking/cost.log by default)
aggregator = CostAggregator()

# Generate reports
daily = aggregator.daily_summary()
weekly = aggregator.weekly_summary()
monthly = aggregator.monthly_summary()
savings = aggregator.savings_report()

# Get overall stats
stats = aggregator.get_summary_stats()
```

### Using a Custom Log File

```python
aggregator = CostAggregator('/path/to/custom/cost.log')
```

### Adding New Cost Entries

```python
aggregator.add_entry({
    'timestamp': '2026-09-25T10:30:00',
    'model': 'claude-opus-4',
    'provider': 'anthropic',
    'input_tokens': 5000,
    'output_tokens': 2000,
    'total_cost_usd': 0.105,
    'worker_id': 'worker-1',
    'workflow_id': 'workflow-x',
    'cache_hit': True,
    'compression_ratio': 0.88,
    'uncompressed_tokens': 8000
})
```

## Log Format

Cost logs are stored in JSONL (JSON Lines) format, with one entry per line:

```json
{
  "timestamp": "2026-09-25T10:30:00",
  "model": "claude-opus-4",
  "provider": "anthropic",
  "input_tokens": 5000,
  "output_tokens": 2000,
  "total_cost_usd": 0.105,
  "worker_id": "worker-1",
  "workflow_id": "workflow-x",
  "task_hash": "abc123def456",
  "cache_hit": true,
  "compression_ratio": 0.88,
  "uncompressed_tokens": 8000
}
```

### Required Fields
- `timestamp`: ISO 8601 timestamp
- `model`: Model name (e.g., "claude-haiku-4.5", "claude-opus-4")
- `provider`: Provider name (e.g., "anthropic", "openai", "google")
- `input_tokens`: Number of input tokens
- `output_tokens`: Number of output tokens
- `total_cost_usd`: Total cost in USD

### Optional Fields
- `worker_id`: Worker identifier
- `workflow_id`: Workflow identifier
- `task_hash`: Hash of the task
- `cache_hit`: Boolean indicating if request hit cache
- `compression_ratio`: Ratio of compressed vs. uncompressed tokens
- `uncompressed_tokens`: Token count before compression

## Report Types

### Daily Summary

Daily cost breakdowns by model with totals and averages per call.

### Weekly Summary

Cost summaries organized by ISO week with per-model metrics.

### Monthly Summary

Cost summaries organized by month for trend analysis.

### Savings Report

Comprehensive report featuring:
- **Compression Metrics**: Token savings and compression ratios
- **Cache Analysis**: Cache hit rates and cost savings
- **Cost Impact**: Estimated savings vs. actual costs
- **Model Breakdown**: Per-model cost and cache performance

## Key Metrics

### Compression Savings

Shows percentage of tokens saved through compression:
```
Compression Ratio = Compressed Tokens / Uncompressed Tokens
Tokens Saved = Uncompressed Tokens - Compressed Tokens
```

### Cache Hit Rate

Percentage of requests that successfully hit cache:
```
Cache Hit Rate = Cache Hits / (Cache Hits + Cache Misses)
Estimated Savings = Cache Hits × 90% of Input Token Cost
```

Cache hits save ~90% because:
- Cached requests reuse already-processed prompts
- Only new output tokens are generated
- Claude's cache feature charges 90% less for cached input

### Total Savings

Combines compression and cache savings:
```
Total Savings = Compression Savings + Cache Hit Savings
Savings % = (Total Savings / Uncompressed Cost) × 100%
```

## Supported Models

### Anthropic
- `claude-opus-4`: $15/1M input, $75/1M output
- `claude-sonnet-4.5`: $3/1M input, $15/1M output
- `claude-haiku-4.5`: $0.80/1M input, $4/1M output

### OpenAI
- `gpt-4o`: $2.50/1M input, $10/1M output
- `gpt-4-turbo`: $10/1M input, $30/1M output
- `gpt-3.5-turbo`: $0.50/1M input, $1.50/1M output

### Google
- `gemini-2.0-flash`: $0.075/1M input, $0.30/1M output
- `gemini-1.5-pro`: $1.25/1M input, $5/1M output

### Other Providers
Mistral, Cohere, AI21, and more. Unknown models default to Haiku pricing.

## Testing

Run the test suite to generate sample data:

```bash
python3 test_aggregator.py
```

This generates:
- Sample log with 115 entries across 30 days
- Realistic distribution: Haiku (50 calls), Sonnet (30), Opus (15), GPT-4o (20)
- Cache hit rates: Haiku 33%, Sonnet 25%, Opus 14%, GPT-4o 20%
- Compression ratios: 0.85-0.92
- JSON report at `~/.claude/cost_tracking/sample_report.json`

### Expected Test Results

```
Sample Report Summary:
  Total entries: 115
  Total cost: $4.73
  Compression savings: $0.09
  Cache hit savings: $0.91
  Total savings: $1.00
  Savings rate: 20.77%
```

## Integration Example

```python
from aggregator import CostAggregator
import json

agg = CostAggregator()
savings = agg.savings_report()

print(f"Total savings: ${savings['cost_summary']['total_savings_usd']:.2f}")
print(f"Cache hit rate: {savings['compression_metrics']['cache_hit_rate']:.1%}")

# Per-model costs
for model, data in savings['model_breakdown'].items():
    print(f"{model}: ${data['total_cost_usd']:.2f} ({data['calls']} calls)")
```

## Architecture

### Core Classes

- **CostEntry**: Dataclass for a single cost log entry
- **ModelPricing**: Static pricing database (cost per 1M tokens)
- **CompressionMetrics**: Compression and cache metrics
- **CostAggregator**: Main aggregation engine

### Key Methods

- `daily_summary()`: Daily cost breakdown
- `weekly_summary()`: Weekly cost breakdown
- `monthly_summary()`: Monthly cost breakdown
- `savings_report()`: Comprehensive savings analysis
- `get_summary_stats()`: Overall statistics
- `add_entry()`: Add and persist new entry

## Performance

- Loading 1,000+ entries: <100ms
- Generating all reports: <200ms
- Memory: ~1KB per entry

## Output Format

All reports are JSON with:
- `period`: Report type
- `generated_at`: ISO 8601 timestamp
- Report-specific data organized by time period

Ready for:
- Dashboard visualization
- CSV export
- Email reports
- Cost alerts
- Metrics export (Prometheus/CloudWatch)

## File Locations

- **Log file**: `~/.claude/cost_tracking/cost.log`
- **Module**: `cost_tracking/aggregator.py`
- **Tests**: `cost_tracking/test_aggregator.py`
- **Sample**: `~/.claude/cost_tracking/sample_report.json`


## Canonical cost contract

All cost dashboards and aggregators use `cost_tracking/api_costs.jsonl` as the
authoritative cost event log. New records are produced by `CostLogger` using
the shared `CostRecord` schema in `cost_tracking/schema.py`.

Pricing calculations are centralized in `cost_tracking/pricing.py`. Dashboards
must consume the recorded `cost_usd` value rather than maintaining their own
pricing tables or silently estimating unknown models.

Readers accept the known historical aliases (`total_cost_usd`,
`prompt_tokens`, `completion_tokens`, and `cost`) so existing log data
remains readable during migration. No second cost log is created by dashboard
or aggregation code.
