"""Small, portable operational metrics stream.

The canonical representation is one JSON object per line. Records contain
execution facts only: no prompts, credentials, private reasoning, or source
payloads. The store is intentionally dependency-free so its JSONL output can
be consumed by PostgreSQL, CSV tools, pandas, or other programs without CE.

This is operational telemetry, not learned state. Learning experiments should
reference these records rather than create a competing execution ledger.
"""

from __future__ import annotations

import csv
import json
import math
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

DEFAULT_PATH = Path(__file__).resolve().parent / "learning" / "operational_metrics.jsonl"
SCHEMA = "operational-metric"
VERSION = "0.1"

_FIELDS = (
    "execution_id",
    "timestamp",
    "service",
    "worker",
    "provider",
    "model",
    "route",
    "task_type",
    "status",
    "latency_ms",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "estimated_cost",
    "quality",
    "outcome",
    "error_type",
)


def _optional_string(value: Any, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string or null")
    return value


def _number(value: Any, name: str, *, integer: bool = False) -> int | float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number or null")
    if integer and not isinstance(value, int):
        raise ValueError(f"{name} must be an integer or null")
    if value < 0:
        raise ValueError(f"{name} must be non-negative")
    return value


@dataclass(frozen=True)
class MetricsRecord:
    """Canonical operational metric for one execution event."""

    execution_id: str
    timestamp: str
    service: str
    worker: str | None = None
    provider: str | None = None
    model: str | None = None
    route: str | None = None
    task_type: str | None = None
    status: str = "unknown"
    latency_ms: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    estimated_cost: float | None = None
    quality: float | None = None
    outcome: str | None = None
    error_type: str | None = None

    def __post_init__(self) -> None:
        for name in ("execution_id", "timestamp", "service", "status"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} must be a non-empty string")
        for name in ("worker", "provider", "model", "route", "task_type", "outcome", "error_type"):
            _optional_string(getattr(self, name), name)
        _number(self.latency_ms, "latency_ms")
        _number(self.input_tokens, "input_tokens", integer=True)
        _number(self.output_tokens, "output_tokens", integer=True)
        _number(self.total_tokens, "total_tokens", integer=True)
        _number(self.estimated_cost, "estimated_cost")
        quality = _number(self.quality, "quality")
        if quality is not None and quality > 1:
            raise ValueError("quality must be between 0 and 1")
        if self.total_tokens is not None and self.input_tokens is not None and self.output_tokens is not None:
            if self.total_tokens != self.input_tokens + self.output_tokens:
                raise ValueError("total_tokens must equal input_tokens + output_tokens")

    @property
    def total_token_count(self) -> int | None:
        """Compatibility-friendly alias for callers that avoid field-name collisions."""
        return self.total_tokens

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "version": VERSION,
            **{field: getattr(self, field) for field in _FIELDS},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MetricsRecord":
        if not isinstance(data, dict):
            raise ValueError("metric record must be a JSON object")
        values = {field: data.get(field) for field in _FIELDS}
        values["execution_id"] = data.get("execution_id")
        values["timestamp"] = data.get("timestamp")
        values["service"] = data.get("service")
        values["status"] = data.get("status", "unknown")
        return cls(**values)


class MetricsStore:
    """Append-only JSONL store with a small CSV export path."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else DEFAULT_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, metric: MetricsRecord) -> dict[str, Any]:
        entry = metric.to_dict()
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n")
        return entry

    def _iter_records(self) -> Iterable[MetricsRecord]:
        """Yield valid records in chronological order without retaining the ledger."""
        if not self.path.exists():
            return
        with self.path.open(encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                try:
                    yield MetricsRecord.from_dict(json.loads(line))
                except (json.JSONDecodeError, TypeError, ValueError):
                    continue

    @staticmethod
    def _validate_limit(limit: int) -> None:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be a positive integer")

    @staticmethod
    def _validate_offset(offset: int) -> None:
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("offset must be a non-negative integer")

    def read(self, limit: int | None = None) -> list[MetricsRecord]:
        """Read records in chronological order; limited reads retain only the newest N."""
        if limit is not None:
            self._validate_limit(limit)
            return list(deque(self._iter_records(), maxlen=limit))
        return list(self._iter_records())

    def read_page(self, limit: int, offset: int = 0) -> list[MetricsRecord]:
        """Read one bounded chronological page, skipping prior valid records."""
        self._validate_limit(limit)
        self._validate_offset(offset)
        page: list[MetricsRecord] = []
        valid_index = 0
        for record in self._iter_records():
            if valid_index >= offset:
                page.append(record)
                if len(page) >= limit:
                    break
            valid_index += 1
        return page

    @staticmethod
    def _spreadsheet_safe_cell(value: Any) -> Any:
        """Neutralize formula-like string cells without changing numeric values."""
        if not isinstance(value, str):
            return value
        first = value.lstrip(" \t\r\n")[:1]
        if value.startswith(("\t", "\r")) or first in {"=", "+", "-", "@"}:
            return "'" + value
        return value

    def export_csv(
        self, destination: str | Path, *, spreadsheet_safe: bool = True
    ) -> Path:
        """Export CSV; safe for spreadsheets by default, with explicit raw opt-out.

        Set spreadsheet_safe=False only for trusted machine consumers that
        require exact text values. Canonical JSONL records are never modified.
        """
        if not isinstance(spreadsheet_safe, bool):
            raise ValueError("spreadsheet_safe must be a boolean")
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=_FIELDS)
            writer.writeheader()
            for metric in self._iter_records():
                row = {field: getattr(metric, field) for field in _FIELDS}
                if spreadsheet_safe:
                    row = {
                        field: self._spreadsheet_safe_cell(value)
                        if isinstance(value, str) else value
                        for field, value in row.items()
                    }
                writer.writerow(row)
        return destination

    def aggregate(self) -> dict[str, Any]:
        """Return deliberately small operational aggregates for service analysis."""
        record_count = 0
        by_service: dict[str, int] = {}
        by_model: dict[str, int] = {}
        by_status: dict[str, int] = {}
        total_cost = 0.0
        total_latency = 0.0
        latency_count = 0
        for record in self._iter_records():
            record_count += 1
            by_service[record.service] = by_service.get(record.service, 0) + 1
            if record.model:
                by_model[record.model] = by_model.get(record.model, 0) + 1
            by_status[record.status] = by_status.get(record.status, 0) + 1
            if record.estimated_cost is not None:
                total_cost += record.estimated_cost
            if record.latency_ms is not None:
                total_latency += record.latency_ms
                latency_count += 1
        return {
            "records": record_count,
            "by_service": by_service,
            "by_model": by_model,
            "by_status": by_status,
            "total_estimated_cost": round(total_cost, 6),
            "average_latency_ms": (total_latency / latency_count) if latency_count else None,
        }


def records_to_rows(records: Iterable[MetricsRecord]) -> list[dict[str, Any]]:
    """Return plain tabular rows without requiring pandas."""
    return [{field: getattr(record, field) for field in _FIELDS} for record in records]
