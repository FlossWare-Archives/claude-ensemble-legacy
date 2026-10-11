"""Minimal, dependency-free experiment definitions and evaluation."""
from __future__ import annotations
import copy
import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

SCHEMA = "experiment-result"
VERSION = "0.1"
DEFAULT_PATH = Path(__file__).resolve().parent / "learning" / "experiments.jsonl"
DIRECTIONS = {"higher", "lower"}

def _jsonable(value: Any) -> Any:
    try:
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("experiment values must be JSON-serializable") from exc
    return value

def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, MappingProxyType):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return copy.deepcopy(value)


def _digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()

def _number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{field} must be a finite number")
    return float(value)

@dataclass(frozen=True)
class Experiment:
    experiment_id: str
    hypothesis: str
    baseline: Any
    variant: Any
    inputs: Any
    measurements: dict[str, dict[str, Any]]

    def __post_init__(self) -> None:
        if not isinstance(self.experiment_id, str) or not self.experiment_id.strip():
            raise ValueError("experiment_id must be a non-empty string")
        if not isinstance(self.hypothesis, str) or not self.hypothesis.strip():
            raise ValueError("hypothesis must be a non-empty string")
        _jsonable(_thaw(self.baseline))
        _jsonable(_thaw(self.variant))
        _jsonable(_thaw(self.inputs))
        if not isinstance(self.measurements, dict) or not self.measurements:
            raise ValueError("measurements must be a non-empty object")
        for name, measurement in self.measurements.items():
            if not isinstance(name, str) or not name.strip():
                raise ValueError("measurement names must be non-empty strings")
            if not isinstance(measurement, dict):
                raise ValueError(f"measurement {name!r} must be an object")
            direction = measurement.get("direction", "higher")
            if direction not in DIRECTIONS:
                raise ValueError(f"measurement {name!r} direction must be 'higher' or 'lower'")
            _number(measurement.get("baseline"), f"{name}.baseline")
            _number(measurement.get("variant"), f"{name}.variant")

        # Frozen dataclasses do not freeze nested dictionaries/lists. Recursively
        # freeze the validated JSON-compatible values so the definition itself cannot
        # change after construction. to_dict() returns a defensive mutable copy.
        object.__setattr__(self, "baseline", _freeze(self.baseline))
        object.__setattr__(self, "variant", _freeze(self.variant))
        object.__setattr__(self, "inputs", _freeze(self.inputs))
        object.__setattr__(self, "measurements", _freeze(self.measurements))

    @classmethod
    def from_dict(cls, data: Any) -> "Experiment":
        if not isinstance(data, dict):
            raise ValueError("experiment must be a JSON object")
        return cls(data.get("experiment_id"), data.get("hypothesis"), data.get("baseline"),
                   data.get("variant"), data.get("inputs", {}), data.get("measurements"))

    def to_dict(self) -> dict[str, Any]:
        return {"experiment_id": self.experiment_id, "hypothesis": self.hypothesis,
                "baseline": _thaw(self.baseline), "variant": _thaw(self.variant),
                "inputs": _thaw(self.inputs), "measurements": _thaw(self.measurements)}

@dataclass(frozen=True)
class ExperimentResult:
    experiment_id: str
    hypothesis: str
    winner: str
    measurements: dict[str, dict[str, Any]]
    input_digest: str
    experiment_digest: str
    schema: str = SCHEMA
    version: str = VERSION

    def to_dict(self) -> dict[str, Any]:
        return {"schema": self.schema, "version": self.version, "experiment_id": self.experiment_id,
                "hypothesis": self.hypothesis, "winner": self.winner, "measurements": self.measurements,
                "input_digest": self.input_digest, "experiment_digest": self.experiment_digest}

    @classmethod
    def from_dict(cls, data: Any) -> "ExperimentResult":
        if not isinstance(data, dict) or data.get("schema") != SCHEMA or data.get("version") != VERSION:
            raise ValueError("unsupported experiment result schema")
        return cls(data.get("experiment_id"), data.get("hypothesis"), data.get("winner"),
                   data.get("measurements"), data.get("input_digest"), data.get("experiment_digest"))

def evaluate(experiment: Experiment) -> ExperimentResult:
    # Evaluate and hash one snapshot so nested mutation cannot make the result
    # describe different values from its digests.
    definition = copy.deepcopy(experiment.to_dict())
    scores = {"baseline": 0.0, "variant": 0.0}
    measurements = {}
    for name, measurement in definition["measurements"].items():
        baseline = _number(measurement["baseline"], f"{name}.baseline")
        variant = _number(measurement["variant"], f"{name}.variant")
        direction = measurement.get("direction", "higher")
        bscore, vscore = (baseline, variant) if direction == "higher" else (-baseline, -variant)
        winner = "variant" if vscore > bscore else "baseline" if bscore > vscore else "tie"
        if winner != "tie":
            scores[winner] += 1.0
        measurements[name] = {"baseline": baseline, "variant": variant,
                              "direction": direction, "winner": winner}
    overall = "variant" if scores["variant"] > scores["baseline"] else "baseline" if scores["baseline"] > scores["variant"] else "tie"
    return ExperimentResult(definition["experiment_id"], definition["hypothesis"], overall, measurements,
                            _digest(definition["inputs"]), _digest(definition))

class ExperimentStore:
    """Append-only JSONL store for lightweight experiment results."""
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else DEFAULT_PATH

    def record(self, result: ExperimentResult) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(result.to_dict(), sort_keys=True) + "\n")

    def get(self, experiment_id: str) -> ExperimentResult | None:
        if not isinstance(experiment_id, str) or not experiment_id or not self.path.exists():
            return None
        for line in reversed(self.path.read_text(encoding="utf-8").splitlines()):
            try:
                data = json.loads(line)
                if data.get("experiment_id") == experiment_id:
                    return ExperimentResult.from_dict(data)
            except (ValueError, TypeError, json.JSONDecodeError):
                continue
        return None
