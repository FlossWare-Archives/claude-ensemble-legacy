"""Canonical cost-tracking record and storage contract.

The repository has one authoritative cost log:
    cost_tracking/api_costs.jsonl

All cost dashboards and aggregators consume this file. Compatibility aliases
are accepted when reading historical records, but newly written records use
the canonical field names below.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


CANONICAL_LOG_PATH = Path(__file__).with_name("api_costs.jsonl")


@dataclass(frozen=True)
class CostRecord:
    """Canonical append-only cost event."""

    timestamp: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    task_name: str = "unknown"
    source: str = "api"
    provider: str = "unknown"
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def total_cost_usd(self) -> float:
        """Compatibility alias for historical consumers."""
        return self.cost_usd

    @property
    def cache_hit(self) -> bool:
        return bool(self.metadata.get("cache_hit", False))

    @property
    def uncompressed_tokens(self) -> int:
        return int(self.metadata.get("uncompressed_tokens", 0))

    @property
    def compression_ratio(self) -> float:
        return float(self.metadata.get("compression_ratio", 1.0))

    @property
    def worker_id(self) -> str | None:
        value = self.metadata.get("worker_id")
        return str(value) if value is not None else None

    @property
    def workflow_id(self) -> str | None:
        value = self.metadata.get("workflow_id")
        return str(value) if value is not None else None

    @property
    def task_hash(self) -> str | None:
        value = self.metadata.get("task_hash")
        return str(value) if value is not None else None

    def to_dict(self) -> dict[str, Any]:
        """Return the canonical JSON representation."""
        return {
            "timestamp": self.timestamp,
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "task_name": self.task_name,
            "source": self.source,
            "provider": self.provider,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CostRecord":
        """Read canonical records and known historical aliases."""
        input_tokens = int(data.get("input_tokens", data.get("prompt_tokens", 0)))
        output_tokens = int(
            data.get("output_tokens", data.get("completion_tokens", 0))
        )

        raw_cost = data.get("cost_usd")
        if raw_cost is None:
            raw_cost = data.get("total_cost_usd", data.get("cost", 0.0))

        task_name = data.get("task_name", data.get("task", "unknown"))
        metadata_value = data.get("metadata", {})
        if isinstance(metadata_value, dict):
            metadata = dict(metadata_value)
        else:
            metadata = {"legacy_metadata": metadata_value}
        # Migrate fields written by the legacy aggregator into the canonical
        # metadata object so historical records remain readable by new code.
        for field_name in (
            "cache_hit",
            "compression_ratio",
            "uncompressed_tokens",
            "worker_id",
            "workflow_id",
            "task_hash",
        ):
            if field_name in data and field_name not in metadata:
                metadata[field_name] = data[field_name]

        return cls(
            timestamp=str(data.get("timestamp", "")),
            model=str(data.get("model", "unknown")),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=float(raw_cost or 0.0),
            task_name=str(task_name),
            source=str(data.get("source", "api")),
            provider=str(data.get("provider", "unknown")),
            metadata=metadata,
        )
