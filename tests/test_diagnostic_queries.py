"""Tests for bounded diagnostic query responses."""
from __future__ import annotations

from unittest.mock import patch

from learning.diagnostic_queries import DiagnosticQueries


def test_problematic_tasks_uses_bounded_default_limit():
    records = [{"content": "x" * 6000, "id": str(i)} for i in range(50)]
    query = DiagnosticQueries()
    with patch.object(query, "semantic_search", return_value={
        "ok": True,
        "data": {"results": records[:10]},
        "sample_count": 10,
    }) as search:
        result = query.find_problematic_tasks()

    search.assert_called_once_with("failed inconclusive low confidence", 10)
    assert len(result["data"]["records"]) == 10
    assert result["sample_count"] == 10


def test_problematic_tasks_clamps_explicit_limit():
    query = DiagnosticQueries()
    with patch.object(query, "semantic_search", return_value={
        "ok": True,
        "data": {"results": []},
        "sample_count": 0,
    }) as search:
        query.find_problematic_tasks(500)

    search.assert_called_once_with("failed inconclusive low confidence", 50)
