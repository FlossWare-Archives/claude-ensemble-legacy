"""
Cost Aggregator Module

Reads append-only cost logs and generates daily, weekly, and monthly cost summaries
with breakdowns by model, compression savings metrics, and cache hit analysis.

Usage:
    from aggregator import CostAggregator

    aggregator = CostAggregator(log_file_path)

    # Generate reports
    daily_report = aggregator.daily_summary()
    weekly_report = aggregator.weekly_summary()
    monthly_report = aggregator.monthly_summary()
    savings_report = aggregator.savings_report()
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Any
from dataclasses import asdict, dataclass

from .schema import CANONICAL_LOG_PATH, CostRecord
from collections import defaultdict
import statistics


@dataclass
class CompressionMetrics:
    """Compression and cache metrics."""
    total_tokens: int
    compressed_tokens: int
    tokens_saved: int
    compression_ratio: float
    cache_hits: int
    cache_misses: int
    cache_hit_rate: float
    estimated_tokens_saved_by_cache: int


class CostAggregator:
    """Aggregates cost data from append-only log files."""

    def __init__(self, log_file_path: Optional[str] = None):
        """Initialize from the single canonical cost log."""
        self.log_file = Path(log_file_path) if log_file_path else CANONICAL_LOG_PATH
        self.entries: List[CostRecord] = []
        self._load_log()

    def _load_log(self) -> None:
        """Load canonical cost records, including supported legacy aliases."""
        if not self.log_file.exists():
            return

        with self.log_file.open(encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                try:
                    self.entries.append(CostRecord.from_dict(json.loads(line)))
                except (json.JSONDecodeError, TypeError, ValueError) as exc:
                    print(f"Warning: Skipping malformed log entry: {exc}")

    def _parse_timestamp(self, ts_str: str) -> datetime:
        """Parse ISO 8601 timestamp string."""
        try:
            return datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
        except (ValueError, AttributeError):
            return datetime.now()

    def _get_date_key(self, timestamp: str) -> str:
        """Extract date (YYYY-MM-DD) from timestamp."""
        dt = self._parse_timestamp(timestamp)
        return dt.strftime('%Y-%m-%d')

    def _get_week_key(self, timestamp: str) -> Tuple[int, int]:
        """Get ISO week (year, week_number) from timestamp."""
        dt = self._parse_timestamp(timestamp)
        iso_cal = dt.isocalendar()
        return (iso_cal.year, iso_cal.week)

    def _get_month_key(self, timestamp: str) -> str:
        """Extract month (YYYY-MM) from timestamp."""
        dt = self._parse_timestamp(timestamp)
        return dt.strftime('%Y-%m')


    def daily_summary(self) -> Dict[str, Any]:
        """
        Generate daily cost summaries.

        Returns:
            Dict containing daily breakdowns by model, total costs, and metrics.
        """
        daily_data: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
            'models': defaultdict(lambda: {
                'calls': 0,
                'input_tokens': 0,
                'output_tokens': 0,
                'total_cost': 0.0,
                'avg_cost': 0.0
            }),
            'total_calls': 0,
            'total_input_tokens': 0,
            'total_output_tokens': 0,
            'total_cost': 0.0
        })

        for entry in self.entries:
            date_key = self._get_date_key(entry.timestamp)
            model_data = daily_data[date_key]['models'][entry.model]

            model_data['calls'] += 1
            model_data['input_tokens'] += entry.input_tokens
            model_data['output_tokens'] += entry.output_tokens
            model_data['total_cost'] += entry.cost_usd

            daily_data[date_key]['total_calls'] += 1
            daily_data[date_key]['total_input_tokens'] += entry.input_tokens
            daily_data[date_key]['total_output_tokens'] += entry.output_tokens
            daily_data[date_key]['total_cost'] += entry.total_cost_usd

        # Calculate averages
        for date_key, data in daily_data.items():
            for model_key, model_data in data['models'].items():
                if model_data['calls'] > 0:
                    model_data['avg_cost'] = round(
                        model_data['total_cost'] / model_data['calls'], 6
                    )
                model_data['total_cost'] = round(model_data['total_cost'], 6)

            data['total_cost'] = round(data['total_cost'], 6)
            # Convert to regular dict for JSON serialization
            data['models'] = dict(data['models'])

        return {
            'period': 'daily',
            'generated_at': datetime.now().isoformat(),
            'daily_summaries': dict(sorted(daily_data.items()))
        }

    def weekly_summary(self) -> Dict[str, Any]:
        """
        Generate weekly cost summaries.

        Returns:
            Dict containing weekly breakdowns by model and total costs.
        """
        weekly_data: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
            'models': defaultdict(lambda: {
                'calls': 0,
                'input_tokens': 0,
                'output_tokens': 0,
                'total_cost': 0.0,
                'avg_cost': 0.0
            }),
            'total_calls': 0,
            'total_input_tokens': 0,
            'total_output_tokens': 0,
            'total_cost': 0.0
        })

        for entry in self.entries:
            year, week = self._get_week_key(entry.timestamp)
            week_key = f"{year}-W{week:02d}"
            model_data = weekly_data[week_key]['models'][entry.model]

            model_data['calls'] += 1
            model_data['input_tokens'] += entry.input_tokens
            model_data['output_tokens'] += entry.output_tokens
            model_data['total_cost'] += entry.total_cost_usd

            weekly_data[week_key]['total_calls'] += 1
            weekly_data[week_key]['total_input_tokens'] += entry.input_tokens
            weekly_data[week_key]['total_output_tokens'] += entry.output_tokens
            weekly_data[week_key]['total_cost'] += entry.total_cost_usd

        # Calculate averages
        for week_key, data in weekly_data.items():
            for model_key, model_data in data['models'].items():
                if model_data['calls'] > 0:
                    model_data['avg_cost'] = round(
                        model_data['total_cost'] / model_data['calls'], 6
                    )
                model_data['total_cost'] = round(model_data['total_cost'], 6)

            data['total_cost'] = round(data['total_cost'], 6)
            data['models'] = dict(data['models'])

        return {
            'period': 'weekly',
            'generated_at': datetime.now().isoformat(),
            'weekly_summaries': dict(sorted(weekly_data.items()))
        }

    def monthly_summary(self) -> Dict[str, Any]:
        """
        Generate monthly cost summaries.

        Returns:
            Dict containing monthly breakdowns by model and total costs.
        """
        monthly_data: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
            'models': defaultdict(lambda: {
                'calls': 0,
                'input_tokens': 0,
                'output_tokens': 0,
                'total_cost': 0.0,
                'avg_cost': 0.0
            }),
            'total_calls': 0,
            'total_input_tokens': 0,
            'total_output_tokens': 0,
            'total_cost': 0.0
        })

        for entry in self.entries:
            month_key = self._get_month_key(entry.timestamp)
            model_data = monthly_data[month_key]['models'][entry.model]

            model_data['calls'] += 1
            model_data['input_tokens'] += entry.input_tokens
            model_data['output_tokens'] += entry.output_tokens
            model_data['total_cost'] += entry.total_cost_usd

            monthly_data[month_key]['total_calls'] += 1
            monthly_data[month_key]['total_input_tokens'] += entry.input_tokens
            monthly_data[month_key]['total_output_tokens'] += entry.output_tokens
            monthly_data[month_key]['total_cost'] += entry.total_cost_usd

        # Calculate averages
        for month_key, data in monthly_data.items():
            for model_key, model_data in data['models'].items():
                if model_data['calls'] > 0:
                    model_data['avg_cost'] = round(
                        model_data['total_cost'] / model_data['calls'], 6
                    )
                model_data['total_cost'] = round(model_data['total_cost'], 6)

            data['total_cost'] = round(data['total_cost'], 6)
            data['models'] = dict(data['models'])

        return {
            'period': 'monthly',
            'generated_at': datetime.now().isoformat(),
            'monthly_summaries': dict(sorted(monthly_data.items()))
        }

    def _calculate_compression_metrics(self) -> CompressionMetrics:
        """Calculate compression and cache metrics."""
        total_tokens = 0
        compressed_tokens = 0
        cache_hits = 0
        cache_misses = 0

        for entry in self.entries:
            actual_tokens = entry.input_tokens + entry.output_tokens
            total_tokens += actual_tokens
            compressed_tokens += actual_tokens

            if entry.uncompressed_tokens > 0:
                compressed_tokens -= actual_tokens
                compressed_tokens += entry.uncompressed_tokens

            if entry.cache_hit:
                cache_hits += 1
            else:
                cache_misses += 1

        tokens_saved = compressed_tokens - total_tokens
        compression_ratio = compressed_tokens / total_tokens if total_tokens > 0 else 1.0
        total_requests = cache_hits + cache_misses
        cache_hit_rate = cache_hits / total_requests if total_requests > 0 else 0.0

        # Estimate tokens saved by cache (approximately 90% of input tokens)
        cached_input_tokens = sum(
            e.input_tokens * 0.9 for e in self.entries if e.cache_hit
        )

        return CompressionMetrics(
            total_tokens=total_tokens,
            compressed_tokens=compressed_tokens,
            tokens_saved=max(0, int(tokens_saved)),
            compression_ratio=round(compression_ratio, 4),
            cache_hits=cache_hits,
            cache_misses=cache_misses,
            cache_hit_rate=round(cache_hit_rate, 4),
            estimated_tokens_saved_by_cache=int(cached_input_tokens)
        )

    def savings_report(self) -> Dict[str, Any]:
        """
        Generate a comprehensive savings report.

        Returns:
            Dict with compression savings, cache hit metrics, and cost impact.
        """
        compression_metrics = self._calculate_compression_metrics()

        # Calculate cost savings
        total_cost = sum(e.total_cost_usd for e in self.entries)

        # Estimate cost if no compression/caching (rough approximation)
        uncompressed_cost = 0.0
        for entry in self.entries:
            if entry.uncompressed_tokens > 0:
                # Calculate what it would cost without compression
                factor = entry.uncompressed_tokens / (entry.input_tokens + entry.output_tokens)
                uncompressed_cost += entry.total_cost_usd * factor
            else:
                uncompressed_cost += entry.total_cost_usd

        # Cache hit cost savings (cache hits cost ~90% less)
        cache_savings = sum(
            e.total_cost_usd * 0.9 for e in self.entries if e.cache_hit
        )

        # Compression cost savings
        compression_savings = uncompressed_cost - total_cost

        # Total savings
        total_savings = cache_savings + compression_savings

        # Model breakdown
        model_breakdown = defaultdict(lambda: {
            'total_cost': 0.0,
            'calls': 0,
            'cache_hits': 0,
            'cache_misses': 0,
            'input_tokens': 0,
            'output_tokens': 0
        })

        for entry in self.entries:
            model_data = model_breakdown[entry.model]
            model_data['total_cost'] += entry.total_cost_usd
            model_data['calls'] += 1
            model_data['input_tokens'] += entry.input_tokens
            model_data['output_tokens'] += entry.output_tokens

            if entry.cache_hit:
                model_data['cache_hits'] += 1
            else:
                model_data['cache_misses'] += 1

        # Format model breakdown
        formatted_breakdown = {}
        for model, data in model_breakdown.items():
            total_requests = data['cache_hits'] + data['cache_misses']
            formatted_breakdown[model] = {
                'total_cost_usd': round(data['total_cost'], 6),
                'calls': data['calls'],
                'cache_hit_rate': round(
                    data['cache_hits'] / total_requests if total_requests > 0 else 0, 4
                ),
                'input_tokens': data['input_tokens'],
                'output_tokens': data['output_tokens']
            }

        return {
            'report_type': 'savings_analysis',
            'generated_at': datetime.now().isoformat(),
            'total_entries_analyzed': len(self.entries),
            'compression_metrics': asdict(compression_metrics),
            'cost_summary': {
                'total_actual_cost_usd': round(total_cost, 6),
                'estimated_uncompressed_cost_usd': round(uncompressed_cost, 6),
                'compression_savings_usd': round(compression_savings, 6),
                'cache_hit_savings_usd': round(cache_savings, 6),
                'total_savings_usd': round(total_savings, 6),
                'savings_percentage': round(
                    (total_savings / uncompressed_cost * 100) if uncompressed_cost > 0 else 0, 2
                )
            },
            'model_breakdown': formatted_breakdown
        }

    def add_entry(self, entry_data: Dict[str, Any]) -> None:
        """
        Add a new cost entry and persist to log.

        Args:
            entry_data: Dict containing cost entry fields.
        """
        metadata = dict(entry_data.get("metadata") or {})
        for field_name in (
            "worker_id",
            "workflow_id",
            "task_hash",
            "cache_hit",
            "compression_ratio",
            "uncompressed_tokens",
        ):
            if field_name in entry_data:
                metadata[field_name] = entry_data[field_name]

        entry = CostRecord(
            timestamp=str(entry_data.get("timestamp", datetime.now().isoformat())),
            model=str(entry_data.get("model", "unknown")),
            provider=str(entry_data.get("provider", "unknown")),
            input_tokens=int(entry_data.get("input_tokens", 0)),
            output_tokens=int(entry_data.get("output_tokens", 0)),
            cost_usd=float(entry_data.get("cost_usd", entry_data.get("total_cost_usd", 0))),
            task_name=str(entry_data.get("task_name", entry_data.get("task", "unknown"))),
            source=str(entry_data.get("source", "api")),
            metadata=metadata,
        )

        self.entries.append(entry)

        # Persist through the same canonical schema used by the logger and reader.
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        with self.log_file.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry.to_dict(), sort_keys=True) + "\n")

    def get_summary_stats(self) -> Dict[str, Any]:
        """Get overall summary statistics."""
        if not self.entries:
            return {
                'total_entries': 0,
                'total_cost': 0.0,
                'date_range': None,
                'models_used': []
            }

        timestamps = [self._parse_timestamp(e.timestamp) for e in self.entries]
        models = set(e.model for e in self.entries)
        total_cost = sum(e.total_cost_usd for e in self.entries)

        return {
            'total_entries': len(self.entries),
            'total_cost_usd': round(total_cost, 6),
            'date_range': {
                'start': min(timestamps).isoformat(),
                'end': max(timestamps).isoformat()
            },
            'models_used': sorted(list(models)),
            'total_input_tokens': sum(e.input_tokens for e in self.entries),
            'total_output_tokens': sum(e.output_tokens for e in self.entries)
        }
