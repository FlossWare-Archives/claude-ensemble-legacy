"""Canonical cost tracking logger."""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, TypeAlias

try:
    from .pricing import CLAUDE_PRICING, calculate_cost
    from .schema import CANONICAL_LOG_PATH, CostRecord
except ImportError:
    from pricing import CLAUDE_PRICING, calculate_cost
    from schema import CANONICAL_LOG_PATH, CostRecord

PRICING = CLAUDE_PRICING
ModelName: TypeAlias = str


class CostLogger:
    """Append canonical API cost records to the repository cost log."""

    def __init__(self, log_path: str | Path | None = None):
        self.log_path = Path(log_path) if log_path is not None else CANONICAL_LOG_PATH
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def calculate_cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        """Calculate cost using the single repository pricing authority."""
        return calculate_cost(model, input_tokens, output_tokens)

    def log_call(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        task_name: str,
        source: str = "api",
        metadata: Optional[dict] = None,
        provider: str = "unknown",
        cost_usd: float | None = None,
    ) -> dict:
        """Append one canonical cost event and return its JSON representation."""
        if cost_usd is None:
            cost_usd = self.calculate_cost(model, input_tokens, output_tokens)
        if cost_usd < 0:
            raise ValueError("cost_usd must be non-negative")

        record = CostRecord(
            timestamp=datetime.now(timezone.utc).isoformat(),
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            task_name=task_name,
            source=source,
            provider=provider,
            metadata=metadata or {},
        )
        entry = record.to_dict()

        with self.log_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry, sort_keys=True) + "\n")

        return entry

    def read_logs(self) -> list[dict]:
        """Read canonical records, accepting known historical field aliases."""
        if not self.log_path.exists():
            return []

        records = []
        with self.log_path.open(encoding="utf-8") as stream:
            for line in stream:
                if line.strip():
                    records.append(CostRecord.from_dict(json.loads(line)).to_dict())
        return records

    def get_stats(self) -> dict:
        """Return aggregate statistics from the canonical cost log."""
        stats = {
            "total_calls": 0,
            "total_tokens": 0,
            "total_cost_usd": 0.0,
            "by_model": {},
            "by_task": {},
        }

        for entry in self.read_logs():
            stats["total_calls"] += 1
            stats["total_tokens"] += entry["total_tokens"]
            stats["total_cost_usd"] += entry["cost_usd"]

            model = entry["model"]
            task = entry["task_name"]
            model_stats = stats["by_model"].setdefault(
                model, {"calls": 0, "tokens": 0, "cost": 0.0}
            )
            task_stats = stats["by_task"].setdefault(
                task, {"calls": 0, "tokens": 0, "cost": 0.0}
            )

            for bucket in (model_stats, task_stats):
                bucket["calls"] += 1
                bucket["tokens"] += entry["total_tokens"]
                bucket["cost"] += entry["cost_usd"]

        stats["total_cost_usd"] = round(stats["total_cost_usd"], 6)
        for bucket in (*stats["by_model"].values(), *stats["by_task"].values()):
            bucket["cost"] = round(bucket["cost"], 6)

        return stats
