"""Lightweight model capability and operational health view."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any

from shared.runtime_config import thompson_state_file

DEFAULT_MODELS = ("haiku", "sonnet", "opus", "gemini-2.5-flash")
DEFAULT_CAPABILITIES = {
    "haiku": ("text_generation", "reasoning", "code"),
    "sonnet": ("text_generation", "reasoning", "code"),
    "opus": ("text_generation", "reasoning", "code"),
    "gemini-2.5-flash": ("text_generation", "reasoning", "code"),
}


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _state_path(path: str | Path | None = None) -> Path:
    return Path(path) if path is not None else thompson_state_file()


def _cost_path(path: str | Path | None = None) -> Path:
    return Path(path) if path is not None else Path(__file__).resolve().parent / "cost_tracking" / "api_costs.jsonl"


def _load_thompson(path: str | Path | None = None) -> dict[str, dict[str, Any]]:
    state_file = _state_path(path)
    if not state_file.exists():
        return {}
    try:
        raw = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    models = raw.get("models", {}) if isinstance(raw, dict) else {}
    return models if isinstance(models, dict) else {}


def _load_recent_costs(path: str | Path | None = None) -> dict[str, dict[str, Any]]:
    log_file = _cost_path(path)
    if not log_file.exists():
        return {}
    result: dict[str, dict[str, Any]] = {}
    try:
        lines = log_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    for line in lines:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict):
            continue
        model = record.get("model")
        if not isinstance(model, str):
            continue
        bucket = result.setdefault(model, {"calls": 0, "cost_usd": 0.0, "last_timestamp": None})
        bucket["calls"] += 1
        cost = _finite_number(record.get("cost_usd", record.get("cost", 0.0)))
        if cost is not None and cost >= 0:
            bucket["cost_usd"] += cost
        timestamp = record.get("timestamp")
        if isinstance(timestamp, str) and (bucket["last_timestamp"] is None or timestamp > bucket["last_timestamp"]):
            bucket["last_timestamp"] = timestamp
    return result


def _provider_for(registry: Any, model: str) -> tuple[str, str]:
    try:
        resolved = registry.resolve_model(model)
    except (ValueError, RuntimeError):
        return "unknown", model
    if not isinstance(resolved, tuple) or len(resolved) != 2:
        return "unknown", model
    provider, canonical = resolved
    if not isinstance(provider, str) or not provider or not isinstance(canonical, str) or not canonical:
        return "unknown", model
    return provider, canonical


def _capabilities_for(model: str) -> tuple[str, ...]:
    configured = os.environ.get("ENSEMBLE_MODEL_CAPABILITIES")
    if configured:
        try:
            raw = json.loads(configured)
            if isinstance(raw, dict) and isinstance(raw.get(model), list):
                values = tuple(item for item in raw[model] if isinstance(item, str) and item)
                if values:
                    return values
        except (ValueError, TypeError):
            pass
    return DEFAULT_CAPABILITIES.get(model, ("text_generation",))


class CapabilityHealthView:
    """Build stable routing metadata without introducing telemetry storage."""

    def __init__(self, registry: Any, *, thompson_path: str | Path | None = None, cost_path: str | Path | None = None, models: tuple[str, ...] = DEFAULT_MODELS) -> None:
        self.registry = registry
        self.thompson_path = thompson_path
        self.cost_path = cost_path
        self.models = models

    def capabilities(self) -> dict[str, Any]:
        entries = []
        for model in self.models:
            provider, canonical = _provider_for(self.registry, model)
            credentials = self.registry.credential_status(provider) if provider != "unknown" else []
            credential_available = any(item.get("state") == "available" for item in credentials)
            entries.append({
                "model": model,
                "canonical_model": canonical,
                "provider": provider,
                "capabilities": list(_capabilities_for(model)),
                "credential_available": credential_available,
            })
        return {"models": entries}

    def health(self) -> dict[str, Any]:
        thompson = _load_thompson(self.thompson_path)
        costs = _load_recent_costs(self.cost_path)
        entries = []
        for model in self.models:
            provider, canonical = _provider_for(self.registry, model)
            credentials = self.registry.credential_status(provider) if provider != "unknown" else []
            credential_available = any(item.get("state") == "available" for item in credentials)
            perf = thompson.get(model) or thompson.get(canonical) or {}
            calls = int(perf.get("calls", 0) or 0)
            successes = int(perf.get("successes", 0) or 0)
            failures = int(perf.get("failures", 0) or 0)
            latency = _finite_number(perf.get("total_latency_ms"))
            total_cost = _finite_number(perf.get("total_cost"))
            recent = costs.get(model) or costs.get(canonical) or {}
            recent_calls = int(recent.get("calls", 0) or 0)
            avg_latency = (latency / calls) if calls > 0 and latency is not None else None
            avg_cost = (total_cost / calls) if calls > 0 and total_cost is not None else None
            success_rate = (successes / calls) if calls > 0 else None
            if provider == "unknown" or not credential_available:
                state = "unavailable" if provider != "unknown" and not credential_available else "unknown"
            elif calls == 0:
                state = "unknown"
            elif failures > successes:
                state = "degraded"
            else:
                state = "available"
            entries.append({
                "model": model,
                "canonical_model": canonical,
                "provider": provider,
                "state": state,
                "credential_available": credential_available,
                "calls": calls,
                "recent_calls": recent_calls,
                "successes": successes,
                "failures": failures,
                "success_rate": success_rate,
                "avg_latency_ms": round(avg_latency, 3) if avg_latency is not None else None,
                "avg_cost_usd": round(avg_cost, 8) if avg_cost is not None else None,
                "last_updated": perf.get("last_updated"),
                "last_activity": recent.get("last_timestamp"),
            })
        return {"models": entries}

    def snapshot(self) -> dict[str, Any]:
        return {"capabilities": self.capabilities(), "health": self.health()}
