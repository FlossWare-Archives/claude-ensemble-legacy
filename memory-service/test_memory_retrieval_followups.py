"""Regression coverage for the execution-aware Memory retrieval contract."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from execution.context import ExecutionContext
from memory_service import MemoryStore


def _context(*, execution_id, request_id="request-1", lineage=(), parent=None):
    return ExecutionContext(
        request_id=request_id,
        execution_id=execution_id,
        parent_execution_id=parent,
        objective="test retrieval",
        lineage=tuple(lineage),
    )


def _append(store, context, result):
    assert store.append_entry(
        "execution-context",
        {"result": result, "execution_context": context.to_dict()},
    )


def test_empty_lineage_is_safe_and_can_use_same_request_fallback(tmp_path):
    store = MemoryStore(tmp_path)
    current = _context(execution_id="current", lineage=())
    prior = _context(execution_id="prior", lineage=())
    _append(store, prior, "prior result")

    results = store.retrieve_entries("execution-context", current)

    assert [item["relation"] for item in results] == ["same-request"]
    assert results[0]["record"]["result"] == "prior result"


def test_malformed_context_is_skipped_without_aborting_retrieval(tmp_path):
    store = MemoryStore(tmp_path)
    current = _context(execution_id="current", lineage=("root", "current"))
    path = tmp_path / "execution-context.jsonl"
    path.write_text(
        json.dumps(
            {
                "result": "malformed",
                "execution_context": {"request_id": "request-1"},
            }
        )
        + "\n"
    )
    _append(
        store,
        _context(
            execution_id="parent",
            lineage=("root", "parent"),
            parent="root",
        ),
        "valid parent",
    )

    results = store.retrieve_entries("execution-context", current)

    assert [item["record"]["result"] for item in results] == ["valid parent"]


def test_duplicate_execution_ids_preserve_persisted_order(tmp_path):
    store = MemoryStore(tmp_path)
    current = _context(execution_id="current", lineage=("root", "current"))
    duplicate_one = _context(execution_id="sibling", lineage=("root", "sibling"))
    duplicate_two = _context(execution_id="sibling", lineage=("root", "sibling"))
    _append(store, duplicate_one, "first observation")
    _append(store, duplicate_two, "second observation")

    results = store.retrieve_entries("execution-context", current)

    assert [item["record"]["result"] for item in results] == [
        "first observation",
        "second observation",
    ]
    assert [item["relation"] for item in results] == [
        "related-lineage",
        "related-lineage",
    ]


def test_relationship_priority_and_stable_tie_breaking(tmp_path):
    store = MemoryStore(tmp_path)
    root = _context(execution_id="root", lineage=("root",))
    parent = root.child(execution_id="parent", stage="parent")
    current = parent.child(execution_id="current", stage="current")
    sibling = root.child(execution_id="sibling", stage="sibling")
    same_request = _context(
        execution_id="same-request",
        lineage=("other-root",),
    )

    _append(store, sibling, "sibling")
    _append(store, same_request, "same request")
    _append(store, root, "ancestor")
    _append(store, parent, "parent")

    results = store.retrieve_entries("execution-context", current, limit=10)

    assert [(item["relation"], item["record"]["result"]) for item in results] == [
        ("parent", "parent"),
        ("ancestor", "ancestor"),
        ("same-request", "same request"),
        ("related-lineage", "sibling"),
    ]


@pytest.mark.parametrize("limit", [0, -1, 101, True, "10"])
def test_retrieval_limit_validation_remains_strict(tmp_path, limit):
    store = MemoryStore(tmp_path)
    current = _context(execution_id="current")

    with pytest.raises(ValueError):
        store.retrieve_entries("execution-context", current, limit=limit)
