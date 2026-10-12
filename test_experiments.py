"""Tests for minimal experiment evaluation."""
import json
import pytest
from experiments import Experiment, ExperimentStore, evaluate

def sample():
    return Experiment(
        "exp-1", "variant improves quality and cost", {"strategy": "current"},
        {"strategy": "candidate"}, {"dataset": "replay-1", "count": 10},
        {"quality": {"baseline": 0.8, "variant": 0.9, "direction": "higher"},
         "cost": {"baseline": 1.0, "variant": 0.8, "direction": "lower"}})

def test_evaluate_is_deterministic_and_prefers_variant():
    result = evaluate(sample())
    assert result.winner == "variant"
    assert result.input_digest == evaluate(sample()).input_digest
    assert result.experiment_digest == evaluate(sample()).experiment_digest
    assert result.to_dict()["schema"] == "experiment-result"

def test_lower_and_tie_are_explicit():
    e = sample()
    e = Experiment(e.experiment_id, e.hypothesis, e.baseline, e.variant, e.inputs,
                   {"latency": {"baseline": 10, "variant": 10, "direction": "lower"}})
    result = evaluate(e)
    assert result.winner == "tie"
    assert result.measurements["latency"]["winner"] == "tie"

def test_store_round_trip(tmp_path):
    store = ExperimentStore(tmp_path / "experiments.jsonl")
    result = evaluate(sample())
    store.record(result)
    assert store.get("exp-1") == result

@pytest.mark.parametrize("field,value", [("experiment_id", ""), ("hypothesis", ""), ("measurements", {})])
def test_required_fields_are_validated(field, value):
    data = sample().to_dict()
    data[field] = value
    with pytest.raises(ValueError):
        Experiment.from_dict(data)

def test_non_finite_measurement_is_rejected():
    data = sample().to_dict()
    data["measurements"]["quality"]["variant"] = float("nan")
    with pytest.raises(ValueError):
        Experiment.from_dict(data)

def test_result_is_json_serializable():
    json.dumps(evaluate(sample()).to_dict(), allow_nan=False)


def test_experiment_fields_and_to_dict_are_defensive():
    inputs = {"dataset": {"name": "replay-1"}}
    measurements = {"quality": {"baseline": 0.8, "variant": 0.9, "direction": "higher"}}
    experiment = Experiment("exp-defensive", "variant improves quality", {}, {}, inputs, measurements)

    try:
        experiment.inputs["dataset"]["name"] = "mutated"
        raise AssertionError("expected experiment inputs to be immutable")
    except TypeError:
        pass
    try:
        experiment.measurements["quality"]["variant"] = 0.1
        raise AssertionError("expected experiment measurements to be immutable")
    except TypeError:
        pass

    exported = experiment.to_dict()
    exported["inputs"]["dataset"]["name"] = "mutated"
    exported["measurements"]["quality"]["variant"] = 0.1

    result = evaluate(experiment)
    assert result.winner == "variant"
    assert result.measurements["quality"]["variant"] == 0.9


def test_experiment_snapshots_nested_inputs_and_measurements():
    inputs = {"dataset": {"name": "replay-1", "count": 10}}
    measurements = {"quality": {"baseline": 0.8, "variant": 0.9, "direction": "higher"}}
    experiment = Experiment("exp-snapshot", "variant improves quality", {}, {}, inputs, measurements)

    inputs["dataset"]["count"] = 999
    measurements["quality"]["variant"] = 0.1

    result = evaluate(experiment)
    assert result.winner == "variant"
    assert result.measurements["quality"]["variant"] == 0.9
    assert result.input_digest == evaluate(experiment).input_digest
    assert result.experiment_digest == evaluate(experiment).experiment_digest

@pytest.mark.parametrize("bad_record", [[], None, 17, "not an object"])
def test_store_skips_non_object_jsonl_records(tmp_path, bad_record):
    store = ExperimentStore(tmp_path / "experiments.jsonl")
    result = evaluate(sample())
    store.record(result)
    with store.path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(bad_record) + "\n")
    assert store.get("exp-1") == result


@pytest.mark.parametrize(
    "mutate",
    [
        lambda data: data.update(winner="neither"),
        lambda data: data.update(measurements=[]),
        lambda data: data.update(input_digest="not-a-digest"),
        lambda data: data.update(experiment_digest=""),
        lambda data: data["measurements"]["quality"].update(winner="baseline"),
        lambda data: data["measurements"]["quality"].update(variant=float("inf")),
    ],
)
def test_result_deserialization_rejects_semantically_invalid_records(mutate):
    from experiments import ExperimentResult

    data = evaluate(sample()).to_dict()
    mutate(data)
    with pytest.raises(ValueError):
        ExperimentResult.from_dict(data)


def test_store_skips_semantically_invalid_matching_record(tmp_path):
    store = ExperimentStore(tmp_path / "experiments.jsonl")
    valid = evaluate(sample()).to_dict()
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(
        json.dumps(valid) + "\n" +
        json.dumps(dict(valid, winner="not-a-winner")) + "\n",
        encoding="utf-8",
    )
    assert store.get("exp-1") == evaluate(sample())

def test_result_measurements_are_recursively_immutable_and_exports_are_detached():
    from experiments import ExperimentResult

    evaluated = evaluate(sample())
    source_measurements = evaluated.to_dict()["measurements"]
    result = ExperimentResult(
        evaluated.experiment_id,
        evaluated.hypothesis,
        evaluated.winner,
        source_measurements,
        evaluated.input_digest,
        evaluated.experiment_digest,
    )

    source_measurements["quality"]["variant"] = 0.1
    assert result.measurements["quality"]["variant"] == 0.9

    with pytest.raises(TypeError):
        result.measurements["quality"]["variant"] = 0.1

    exported = result.to_dict()
    exported["measurements"]["quality"]["variant"] = 0.2
    assert result.measurements["quality"]["variant"] == 0.9
    assert json.dumps(result.to_dict(), allow_nan=False)
