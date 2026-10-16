"""Small, portable provenance records for important CE decisions.\n\nThe schema rejects sensitive field names and private-reasoning fields, but it\ndoes not inspect arbitrary string values for secrets or private content.\nCallers remain responsible for supplying only safe provenance values.\n"""
from __future__ import annotations

import json
import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

SCHEMA = "decision-provenance"
VERSION = "0.1"
DEFAULT_PATH = Path(__file__).resolve().parent / "learning" / "decision-provenance.jsonl"

_POLICY_KEYS = {
    "allowed_models",
    "allowed_providers",
    "max_cost_usd",
    "max_latency_ms",
    "required_capabilities",
    "min_confidence",
    "allow_fallback",
    "allow_escalation",
}
_STRATEGY_KEYS = {"name", "version", "algorithm", "variant"}
_EVIDENCE_KEYS = {"source", "metric", "value", "confidence", "timestamp"}
_DECISION_KEYS = {"model", "provider", "route", "name", "id", "version"}

_SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "credentials",
    "password",
    "private_key",
    "private_reasoning",
    "prompt",
    "reasoning",
    "secret",
    "token",
}


def _deep_freeze(value: Any) -> Any:
    """Recursively freeze validated JSON data so records cannot change after validation."""
    if isinstance(value, Mapping):
        return MappingProxyType({key: _deep_freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_deep_freeze(item) for item in value)
    return value


def _deep_thaw(value: Any) -> Any:
    """Return detached JSON-compatible data for serialization and callers."""
    if isinstance(value, Mapping):
        return {key: _deep_thaw(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_deep_thaw(item) for item in value]
    return value


def _object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a JSON object")
    return value


def _list(value: Any, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be a JSON array")
    return value


def _scalar(value: Any, name: str) -> Any:
    if isinstance(value, (dict, list)):
        raise ValueError(f"{name} must contain only JSON scalar values")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{name} contains a non-finite number")
    return value


def _safe_mapping(value: Any, name: str, allowed_keys: set[str]) -> dict[str, Any]:
    mapping = _object(value, name)
    for key, item in mapping.items():
        if not isinstance(key, str):
            raise ValueError(f"{name} keys must be strings")
        if key.lower() in _SENSITIVE_KEYS:
            raise ValueError(f"{name} contains a prohibited field: {key}")
        if key not in allowed_keys:
            raise ValueError(f"{name} contains unsupported field: {key}")
        if isinstance(item, (dict, list)):
            if name == "policy" and key in {"allowed_models", "allowed_providers", "required_capabilities"}:
                for entry in _list(item, f"{name}.{key}"):
                    _scalar(entry, f"{name}.{key}")
            else:
                raise ValueError(f"{name}.{key} must be a JSON scalar")
        else:
            _scalar(item, f"{name}.{key}")
    return dict(mapping)


def _decision_value(value: Any, name: str) -> Any:
    if isinstance(value, dict):
        mapping = _object(value, name)
        for key, item in mapping.items():
            if not isinstance(key, str):
                raise ValueError(f"{name} keys must be strings")
            if key.lower() in _SENSITIVE_KEYS:
                raise ValueError(f"{name} contains a prohibited field: {key}")
            if key not in _DECISION_KEYS:
                raise ValueError(f"{name} contains unsupported field: {key}")
            _scalar(item, f"{name}.{key}")
        return dict(mapping)
    return _scalar(value, name)


def _validate_evidence(value: Any) -> list[dict[str, Any]]:
    evidence = _list(value, "evidence")
    validated: list[dict[str, Any]] = []
    for index, item in enumerate(evidence):
        mapping = _safe_mapping(item, f"evidence[{index}]", _EVIDENCE_KEYS)
        validated.append(mapping)
    return validated


@dataclass(frozen=True)
class DecisionRecord:
    """Versioned decision facts with explicit fields and no reasoning field.\n\n    Sensitive field names are rejected, but arbitrary string values are not\n    semantically inspected. Callers must not place credentials or private\n    reasoning in otherwise valid string fields.\n    """

    decision_id: str
    execution_id: str
    decision_type: str
    selected: Any
    alternatives: tuple[Any, ...] = field(default_factory=tuple)
    policy: Mapping[str, Any] = field(default_factory=dict)
    strategy: Mapping[str, Any] = field(default_factory=dict)
    evidence: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    created_at: str = ""
    schema: str = SCHEMA
    version: str = VERSION

    def __post_init__(self) -> None:
        # The public dataclass constructor must preserve the same snapshot
        # immutability guarantee as create() and from_dict().
        object.__setattr__(self, "selected", _deep_freeze(self.selected))
        object.__setattr__(self, "alternatives", _deep_freeze(self.alternatives))
        object.__setattr__(self, "policy", _deep_freeze(self.policy))
        object.__setattr__(self, "strategy", _deep_freeze(self.strategy))
        object.__setattr__(self, "evidence", _deep_freeze(self.evidence))

    @classmethod
    def create(
        cls,
        *,
        execution_id: str,
        decision_type: str,
        selected: Any,
        alternatives: list[Any] | None = None,
        policy: dict[str, Any] | None = None,
        strategy: dict[str, Any] | None = None,
        evidence: list[dict[str, Any]] | None = None,
    ) -> "DecisionRecord":
        if not isinstance(execution_id, str) or not execution_id:
            raise ValueError("execution_id is required")
        if not isinstance(decision_type, str) or not decision_type:
            raise ValueError("decision_type is required")
        if selected is None:
            raise ValueError("selected is required")
        alternatives_value = alternatives if alternatives is not None else []
        policy_value = policy if policy is not None else {}
        strategy_value = strategy if strategy is not None else {}
        evidence_value = evidence if evidence is not None else []
        alternatives_list = _list(alternatives_value, "alternatives")
        safe_alternatives = [
            _decision_value(item, f"alternatives[{index}]")
            for index, item in enumerate(alternatives_list)
        ]
        safe_selected = _decision_value(selected, "selected")
        safe_policy = _safe_mapping(policy_value, "policy", _POLICY_KEYS)
        safe_strategy = _safe_mapping(strategy_value, "strategy", _STRATEGY_KEYS)
        safe_evidence = _validate_evidence(evidence_value)
        return cls(
            decision_id=str(uuid.uuid4()),
            execution_id=execution_id,
            decision_type=decision_type,
            selected=_deep_freeze(safe_selected),
            alternatives=tuple(_deep_freeze(item) for item in safe_alternatives),
            policy=_deep_freeze(safe_policy),
            strategy=_deep_freeze(safe_strategy),
            evidence=tuple(_deep_freeze(item) for item in safe_evidence),
            created_at=datetime.now(timezone.utc).isoformat(),
        )

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "DecisionRecord":
        _object(value, "record")
        if value.get("schema") != SCHEMA or value.get("version") != VERSION:
            raise ValueError("unsupported decision provenance schema")
        decision_id = value.get("decision_id")
        execution_id = value.get("execution_id")
        decision_type = value.get("decision_type")
        created_at = value.get("created_at")
        if not all(isinstance(item, str) and item for item in (decision_id, execution_id, decision_type, created_at)):
            raise ValueError("decision provenance identifiers and timestamp must be non-empty strings")
        record = cls.create(
            execution_id=execution_id,
            decision_type=decision_type,
            selected=value.get("selected"),
            alternatives=value.get("alternatives", []),
            policy=value.get("policy", {}),
            strategy=value.get("strategy", {}),
            evidence=value.get("evidence", []),
        )
        return cls(
            decision_id=decision_id,
            execution_id=record.execution_id,
            decision_type=record.decision_type,
            selected=record.selected,
            alternatives=record.alternatives,
            policy=record.policy,
            strategy=record.strategy,
            evidence=record.evidence,
            created_at=created_at,
            schema=SCHEMA,
            version=VERSION,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "version": self.version,
            "decision_id": self.decision_id,
            "execution_id": self.execution_id,
            "decision_type": self.decision_type,
            "selected": _deep_thaw(self.selected),
            "alternatives": _deep_thaw(self.alternatives),
            "policy": _deep_thaw(self.policy),
            "strategy": _deep_thaw(self.strategy),
            "evidence": _deep_thaw(self.evidence),
            "created_at": self.created_at,
        }


class DecisionProvenanceStore:
    """Append-only JSONL store with simple lookup by decision identifier."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else DEFAULT_PATH

    def record(self, decision: DecisionRecord) -> DecisionRecord:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(decision.to_dict(), sort_keys=True) + "\n")
        return decision

    def get(self, decision_id: str) -> DecisionRecord | None:
        if not self.path.exists():
            return None
        try:
            with self.path.open(encoding="utf-8") as handle:
                lines = handle.readlines()
        except OSError:
            return None
        for line in reversed(lines):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
                if isinstance(value, dict) and value.get("decision_id") == decision_id:
                    return DecisionRecord.from_dict(value)
            except (ValueError, KeyError, TypeError, json.JSONDecodeError):
                continue
        return None
