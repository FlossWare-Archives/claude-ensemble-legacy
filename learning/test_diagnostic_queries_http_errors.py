"""HTTP error handling contract for Memory REST diagnostic callers."""

from __future__ import annotations

from io import BytesIO
from urllib.error import HTTPError
from unittest.mock import patch

import pytest

from learning.diagnostic_queries import DiagnosticQueries


def test_memory_rest_http_error_is_reported_as_service_unavailable():
    error = HTTPError(
        "http://127.0.0.1:8767/memory/search",
        404,
        "not found",
        {},
        BytesIO(b'{"ok": false, "error_type": "not_found"}'),
    )
    with patch("learning.diagnostic_queries.urllib.request.urlopen", side_effect=error):
        with pytest.raises(RuntimeError, match="service unavailable"):
            DiagnosticQueries(memory_service_url="http://127.0.0.1:8767").semantic_search("needle")
