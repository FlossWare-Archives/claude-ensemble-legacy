"""Tests for the lightweight policy evaluator."""
from __future__ import annotations

import math

from policy import Policy, evaluate


def test_policy_allows_matching_request() -> None:
    policy = Policy(
        allowed_models=("sonnet",),
        allowed_providers=("anthropic",),
        max_cost_usd=1.0,
        max_latency_ms=5000,
        required_capabilities=("code",),
        min_confidence=0.7,
    )
    result = evaluate(
        {
            "model": "sonnet",
            "provider": "anthropic",
            "capabilities": ["code", "reasoning"],
            "estimated_cost_usd": 0.25,
            "estimated_latency_ms": 1000,
            "confidence": 0.9,
        },
        policy,
    )
    assert result.allowed
    assert result.reasons == ()


def test_policy_rejects_and_offers_fallback() -> None:
    policy = Policy(allowed_models=("sonnet",), max_cost_usd=1.0)
    result = evaluate({"model": "opus", "estimated_cost_usd": 2.0}, policy)
    assert not result.allowed
    assert "model is not allowed" in result.reasons
    assert "estimated cost exceeds policy limit" in result.reasons
    assert result.actions == ("fallback", "escalate")


def test_policy_can_disable_recovery_actions() -> None:
    policy = Policy(
        allowed_models=("sonnet",),
        allow_fallback=False,
        allow_escalation=False,
    )
    result = evaluate({"model": "opus"}, policy)
    assert not result.allowed
    assert result.actions == ()


def test_policy_rejects_missing_values_for_active_limits() -> None:
    result = evaluate(
        {"model": "sonnet"},
        Policy(max_cost_usd=1.0, max_latency_ms=1000, min_confidence=0.7),
    )
    assert not result.allowed
    assert result.reasons == (
        "estimated cost is required by policy",
        "estimated latency is required by policy",
        "confidence is required by policy",
    )


def test_policy_rejects_non_finite_request_values() -> None:
    policy = Policy(max_cost_usd=1.0, max_latency_ms=1000, min_confidence=0.7)
    for field, value, reason in (
        ("estimated_cost_usd", math.nan, "estimated_cost_usd must be finite"),
        ("estimated_latency_ms", math.inf, "estimated_latency_ms must be finite"),
        ("confidence", -math.inf, "confidence must be finite"),
    ):
        result = evaluate({field: value}, policy)
        assert not result.allowed
        assert reason in result.reasons


def test_policy_from_dict_validates_input() -> None:
    try:
        Policy.from_dict({"allowed_models": "sonnet"})
    except ValueError as exc:
        assert "allowed_models" in str(exc)
    else:
        raise AssertionError("expected validation failure")


def test_policy_from_dict_rejects_non_finite_and_invalid_booleans() -> None:
    for field, value in (
        ("max_cost_usd", math.nan),
        ("max_latency_ms", math.inf),
        ("min_confidence", -math.inf),
    ):
        try:
            Policy.from_dict({field: value})
        except ValueError as exc:
            assert "finite" in str(exc)
        else:
            raise AssertionError("expected non-finite validation failure")

    for field in ("allow_fallback", "allow_escalation"):
        try:
            Policy.from_dict({field: "false"})
        except ValueError as exc:
            assert "boolean" in str(exc)
        else:
            raise AssertionError("expected boolean validation failure")

    try:
        Policy.from_dict({"min_confidence": 1.1})
    except ValueError as exc:
        assert "<= 1" in str(exc)
    else:
        raise AssertionError("expected confidence range validation failure")

    try:
        Policy.from_dict([])
    except ValueError as exc:
        assert "JSON object" in str(exc)
    else:
        raise AssertionError("expected policy shape validation failure")



def test_policy_rejects_impossible_supplied_telemetry_even_without_thresholds() -> None:
    for field, value, reason in (
        ("estimated_cost_usd", -1, "estimated_cost_usd must be >= 0"),
        ("estimated_latency_ms", -1, "estimated_latency_ms must be >= 0"),
        ("confidence", -0.1, "confidence must be >= 0"),
        ("confidence", 1.1, "confidence must be <= 1"),
        ("estimated_cost_usd", True, "estimated_cost_usd must be a number"),
        ("estimated_latency_ms", math.nan, "estimated_latency_ms must be finite"),
        ("confidence", math.inf, "confidence must be finite"),
    ):
        result = evaluate({field: value}, Policy())
        assert not result.allowed
        assert reason in result.reasons


def test_policy_accepts_valid_telemetry_boundaries() -> None:
    result = evaluate(
        {"estimated_cost_usd": 0, "estimated_latency_ms": 0, "confidence": 1},
        Policy(max_cost_usd=0, max_latency_ms=0, min_confidence=1),
    )
    assert result.allowed
    assert result.reasons == ()
