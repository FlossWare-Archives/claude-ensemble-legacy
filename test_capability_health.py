"""Tests for the model capability and health view."""
from __future__ import annotations

import json

from capability_health import CapabilityHealthView


class FakeRegistry:
    def resolve_model(self, model):
        if model.startswith("gemini"):
            return "google", model
        return "anthropic", model

    def credential_status(self, provider):
        return [{"name": "default", "state": "available", "source": "env:KEY"}]


def test_capabilities_are_stable_and_provider_aware() -> None:
    view = CapabilityHealthView(FakeRegistry(), models=("sonnet", "gemini-2.5-flash"))
    result = view.capabilities()
    assert result["models"][0]["provider"] == "anthropic"
    assert result["models"][0]["credential_available"] is True
    assert result["models"][1]["provider"] == "google"


def test_health_combines_thompson_and_cost_data(tmp_path) -> None:
    state = tmp_path / "thompson.json"
    state.write_text(json.dumps({"models": {"sonnet": {
        "calls": 4, "successes": 3, "failures": 1,
        "total_latency_ms": 800, "total_cost": 0.4,
        "last_updated": "2026-10-03T20:00:00+00:00",
    }}}))
    costs = tmp_path / "costs.jsonl"
    costs.write_text(json.dumps({
        "timestamp": "2026-10-03T20:01:00+00:00", "model": "sonnet", "cost_usd": 0.1,
    }) + "\n")
    entry = CapabilityHealthView(FakeRegistry(), thompson_path=state, cost_path=costs, models=("sonnet",)).health()["models"][0]
    assert entry["state"] == "available"
    assert entry["success_rate"] == 0.75
    assert entry["avg_latency_ms"] == 200.0
    assert entry["avg_cost_usd"] == 0.1


def test_health_marks_model_degraded_when_failures_dominate(tmp_path) -> None:
    state = tmp_path / "thompson.json"
    state.write_text(json.dumps({"models": {"opus": {
        "calls": 5, "successes": 1, "failures": 4, "total_latency_ms": 100, "total_cost": 0.5,
    }}}))
    entry = CapabilityHealthView(FakeRegistry(), thompson_path=state, cost_path=tmp_path / "missing.jsonl", models=("opus",)).health()["models"][0]
    assert entry["state"] == "degraded"


def test_health_reports_unknown_measurements_without_history() -> None:
    entry = CapabilityHealthView(FakeRegistry(), models=("haiku",)).health()["models"][0]
    assert entry["state"] == "unknown"
    assert entry["credential_available"] is True
    assert entry["calls"] == 0
    assert entry["success_rate"] is None
    assert entry["avg_latency_ms"] is None
    assert entry["avg_cost_usd"] is None


def test_health_reports_unknown_when_aggregate_metrics_are_missing(tmp_path) -> None:
    state = tmp_path / "thompson.json"
    state.write_text(json.dumps({"models": {"sonnet": {"calls": 2, "successes": 2, "failures": 0}}}))
    entry = CapabilityHealthView(FakeRegistry(), thompson_path=state, models=("sonnet",)).health()["models"][0]
    assert entry["state"] == "available"
    assert entry["avg_latency_ms"] is None
    assert entry["avg_cost_usd"] is None


def test_health_marks_missing_credentials_unavailable() -> None:
    class NoCredentials(FakeRegistry):
        def credential_status(self, provider):
            return []

    entry = CapabilityHealthView(NoCredentials(), models=("sonnet",)).health()["models"][0]
    assert entry["state"] == "unavailable"
    assert entry["credential_available"] is False


def test_unknown_model_does_not_guess_provider() -> None:
    class UnknownRegistry(FakeRegistry):
        def resolve_model(self, model):
            raise ValueError("unknown model")

    entry = CapabilityHealthView(UnknownRegistry(), models=("mystery",)).health()["models"][0]
    assert entry["provider"] == "unknown"
    assert entry["state"] == "unknown"
    assert entry["credential_available"] is False


def test_snapshot_is_json_safe() -> None:
    json.dumps(CapabilityHealthView(FakeRegistry(), models=("sonnet",)).snapshot())


def test_health_uses_shared_runtime_thompson_path(tmp_path, monkeypatch) -> None:
    state = tmp_path / "configured-thompson.json"
    state.write_text(json.dumps({"models": {"sonnet": {
        "calls": 4, "successes": 3, "failures": 1,
        "total_latency_ms": 800, "total_cost": 0.4,
    }}}))
    monkeypatch.setenv("ENSEMBLE_THOMPSON_STATE_FILE", str(state))
    entry = CapabilityHealthView(FakeRegistry(), models=("sonnet",)).health()["models"][0]
    assert entry["calls"] == 4
    assert entry["successes"] == 3
    assert entry["failures"] == 1
    assert entry["success_rate"] == 0.75

