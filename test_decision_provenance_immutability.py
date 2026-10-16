"""Regression tests for immutable decision-provenance snapshots."""

import json

import pytest

from decision_provenance import DecisionProvenanceStore, DecisionRecord


def test_input_mutations_cannot_change_validated_record(tmp_path):
    evidence = [{"source": "unit-test", "metric": "quality", "value": 0.9}]
    policy = {"allowed_models": ["sonnet", "haiku"]}
    selected = {"model": "sonnet", "provider": "claude-code"}
    record = DecisionRecord.create(
        execution_id="exec-1",
        decision_type="routing",
        selected=selected,
        policy=policy,
        evidence=evidence,
    )

    evidence[0]["source"] = "tampered"
    evidence[0]["api_key"] = "sentinel"
    policy["allowed_models"].append("unapproved")
    selected["model"] = "gpt-4"

    exported = record.to_dict()
    assert exported["evidence"] == [
        {"source": "unit-test", "metric": "quality", "value": 0.9}
    ]
    assert exported["policy"]["allowed_models"] == ["sonnet", "haiku"]
    assert exported["selected"]["model"] == "sonnet"

    store = DecisionProvenanceStore(tmp_path / "provenance.jsonl")
    store.record(record)
    persisted = json.loads((tmp_path / "provenance.jsonl").read_text().strip())
    assert persisted["evidence"][0]["source"] == "unit-test"
    assert "api_key" not in persisted["evidence"][0]


def test_record_nested_values_are_immutable():
    record = DecisionRecord.create(
        execution_id="exec-2",
        decision_type="routing",
        selected={"model": "sonnet"},
        policy={"allowed_models": ["sonnet"]},
        evidence=[{"source": "unit-test", "metric": "quality", "value": 0.9}],
    )

    with pytest.raises(TypeError):
        record.selected["model"] = "gpt-4"
    with pytest.raises(TypeError):
        record.evidence[0]["api_key"] = "sentinel"
    with pytest.raises(AttributeError):
        record.evidence.append({"source": "tampered"})
    with pytest.raises(AttributeError):
        record.policy["allowed_models"].append("gpt-4")


def test_exported_dict_is_detached_from_record():
    record = DecisionRecord.create(
        execution_id="exec-3",
        decision_type="routing",
        selected={"model": "sonnet"},
        policy={"allowed_models": ["sonnet"]},
        evidence=[{"source": "unit-test", "metric": "quality", "value": 0.9}],
    )

    exported = record.to_dict()
    exported["selected"]["model"] = "gpt-4"
    exported["policy"]["allowed_models"].append("gpt-4")
    exported["evidence"][0]["api_key"] = "sentinel"

    assert record.to_dict()["selected"]["model"] == "sonnet"
    assert record.to_dict()["policy"]["allowed_models"] == ["sonnet"]
    assert "api_key" not in record.to_dict()["evidence"][0]



def test_public_dataclass_constructor_also_freezes_nested_fields():
    selected = {"model": "sonnet"}
    policy = {"allowed_models": ["sonnet"]}
    evidence = [{"source": "unit-test", "metric": "quality", "value": 0.9}]
    record = DecisionRecord(
        decision_id="decision-1",
        execution_id="exec-direct",
        decision_type="routing",
        selected=selected,
        alternatives=[{"model": "haiku"}],
        policy=policy,
        strategy={"name": "baseline"},
        evidence=evidence,
        created_at="2026-01-01T00:00:00+00:00",
    )

    selected["model"] = "gpt-4"
    policy["allowed_models"].append("gpt-4")
    evidence[0]["source"] = "tampered"

    assert record.to_dict()["selected"]["model"] == "sonnet"
    assert record.to_dict()["policy"]["allowed_models"] == ["sonnet"]
    assert record.to_dict()["evidence"][0]["source"] == "unit-test"
    with pytest.raises(TypeError):
        record.selected["model"] = "gpt-4"
    with pytest.raises(AttributeError):
        record.alternatives.append({"model": "gpt-4"})
