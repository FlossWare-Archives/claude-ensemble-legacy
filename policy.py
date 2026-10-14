"""Lightweight policy evaluation for Ensemble decisions.

The policy boundary deliberately uses plain dictionaries and deterministic
rules. It is a guardrail, not a policy language or framework.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any


@dataclass(frozen=True)
class Policy:
    allowed_models: tuple[str, ...] = ()
    allowed_providers: tuple[str, ...] = ()
    max_cost_usd: float | None = None
    max_latency_ms: float | None = None
    required_capabilities: tuple[str, ...] = ()
    min_confidence: float | None = None
    allow_fallback: bool = True
    allow_escalation: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed_models": list(self.allowed_models),
            "allowed_providers": list(self.allowed_providers),
            "max_cost_usd": self.max_cost_usd,
            "max_latency_ms": self.max_latency_ms,
            "required_capabilities": list(self.required_capabilities),
            "min_confidence": self.min_confidence,
            "allow_fallback": self.allow_fallback,
            "allow_escalation": self.allow_escalation,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Policy":
        if not isinstance(value, dict):
            raise ValueError("policy must be a JSON object")

        def strings(name: str) -> tuple[str, ...]:
            raw = value.get(name, ())
            if not isinstance(raw, (list, tuple)):
                raise ValueError(f"{name} must be a list")
            if any(not isinstance(item, str) or not item for item in raw):
                raise ValueError(f"{name} must contain non-empty strings")
            return tuple(raw)

        def number(name: str, minimum: float | None = None) -> float | None:
            raw = value.get(name)
            if raw is None:
                return None
            if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                raise ValueError(f"{name} must be a number")
            result = float(raw)
            if not math.isfinite(result):
                raise ValueError(f"{name} must be finite")
            if minimum is not None and result < minimum:
                raise ValueError(f"{name} must be >= {minimum}")
            return result

        def boolean(name: str, default: bool) -> bool:
            raw = value.get(name, default)
            if not isinstance(raw, bool):
                raise ValueError(f"{name} must be a boolean")
            return raw

        min_confidence = number("min_confidence", 0)
        if min_confidence is not None and min_confidence > 1:
            raise ValueError("min_confidence must be <= 1")

        return cls(
            allowed_models=strings("allowed_models"),
            allowed_providers=strings("allowed_providers"),
            max_cost_usd=number("max_cost_usd", 0),
            max_latency_ms=number("max_latency_ms", 0),
            required_capabilities=strings("required_capabilities"),
            min_confidence=min_confidence,
            allow_fallback=boolean("allow_fallback", True),
            allow_escalation=boolean("allow_escalation", True),
        )


@dataclass(frozen=True)
class PolicyResult:
    allowed: bool
    reasons: tuple[str, ...] = ()
    actions: tuple[str, ...] = ()
    policy: Policy = field(default_factory=Policy)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": True,
            "allowed": self.allowed,
            "reasons": list(self.reasons),
            "actions": list(self.actions),
            "policy": self.policy.to_dict(),
        }


def evaluate(request: dict[str, Any], policy: Policy) -> PolicyResult:
    """Evaluate an execution proposal against policy.

    Every supplied cost, latency, and confidence estimate is validated even if
    its threshold is disabled. Missing values are required only by active
    thresholds. Invalid supplied telemetry produces a structured denial.
    """
    reasons: list[str] = []
    actions: list[str] = []

    model = request.get("model")
    provider = request.get("provider")
    capabilities = request.get("capabilities", [])
    raw_cost = request.get("estimated_cost_usd")
    raw_latency = request.get("estimated_latency_ms")
    raw_confidence = request.get("confidence")

    # Validate every supplied estimate even when its corresponding policy
    # threshold is disabled. Missing values are only required by active limits.
    def telemetry_number(
        name: str,
        value: Any,
        *,
        minimum: float | None = None,
        maximum: float | None = None,
    ) -> float | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            reasons.append(f"{name} must be a number")
            return None
        try:
            number = float(value)
        except (OverflowError, ValueError):
            reasons.append(f"{name} must be finite")
            return None
        if not math.isfinite(number):
            reasons.append(f"{name} must be finite")
            return None
        if minimum is not None and number < minimum:
            reasons.append(f"{name} must be >= {minimum}")
            return None
        if maximum is not None and number > maximum:
            reasons.append(f"{name} must be <= {maximum}")
            return None
        return number

    cost = telemetry_number("estimated_cost_usd", raw_cost, minimum=0)
    latency = telemetry_number("estimated_latency_ms", raw_latency, minimum=0)
    confidence = telemetry_number("confidence", raw_confidence, minimum=0, maximum=1)

    if policy.allowed_models and model not in policy.allowed_models:
        reasons.append("model is not allowed")
    if policy.allowed_providers and provider not in policy.allowed_providers:
        reasons.append("provider is not allowed")

    if not isinstance(capabilities, list):
        reasons.append("capabilities must be a list")
    else:
        missing = [item for item in policy.required_capabilities if item not in capabilities]
        if missing:
            reasons.append(f"required capabilities missing: {', '.join(missing)}")

    if policy.max_cost_usd is not None:
        if raw_cost is None:
            reasons.append("estimated cost is required by policy")
        elif cost is not None and cost > policy.max_cost_usd:
            reasons.append("estimated cost exceeds policy limit")

    if policy.max_latency_ms is not None:
        if raw_latency is None:
            reasons.append("estimated latency is required by policy")
        elif latency is not None and latency > policy.max_latency_ms:
            reasons.append("estimated latency exceeds policy limit")

    if policy.min_confidence is not None:
        if raw_confidence is None:
            reasons.append("confidence is required by policy")
        elif confidence is not None and confidence < policy.min_confidence:
            reasons.append("confidence is below policy threshold")

    if reasons:
        if policy.allow_fallback:
            actions.append("fallback")
        if policy.allow_escalation:
            actions.append("escalate")

    return PolicyResult(not reasons, tuple(reasons), tuple(actions), policy)
