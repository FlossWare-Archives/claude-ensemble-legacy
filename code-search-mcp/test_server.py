#!/usr/bin/env python3
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sys

sys.path.insert(0, str(Path(__file__).parent))
import server


class CodeSearchTest(unittest.TestCase):
    def test_path_cannot_escape_repository(self):
        with self.assertRaises(ValueError):
            server.safe_path(Path("/repo"), "../outside")

    def test_tools_list_exposes_search(self):
        response = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        names = [tool["name"] for tool in response["result"]["tools"]]
        self.assertIn("search_code", names)

    def test_search_parses_rg_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rg_output = json.dumps({
                "type": "match",
                "data": {
                    "path": {"text": "src/example.py"},
                    "line_number": 12,
                    "lines": {"text": "needle here\n"},
                },
            })
            completed = type("Completed", (), {
                "returncode": 0, "stdout": rg_output, "stderr": ""
            })()
            with patch.object(server, "repo_root", return_value=root):
                with patch.object(server.subprocess, "run", return_value=completed) as run:
                    with patch.object(server, "cache_get", return_value=None), patch.object(server, "cache_put"):
                        result = server.search_code({"pattern": "needle"})
            self.assertEqual(result["count"], 1)
            self.assertEqual(result["matches"][0]["line"], 12)
            command = run.call_args.args[0]
            self.assertIn("--json", command)
            self.assertEqual(command[-2:], ["needle", "."])

    def test_cache_key_includes_repository_root(self):
        query = {"pattern": "needle", "path": ".", "max_results": 50}
        self.assertNotEqual(
            server.cache_key(Path("/repo-a"), query),
            server.cache_key(Path("/repo-b"), query),
        )

    def test_cache_hit_skips_rg(self):
        cached = {"matches": [{"path": "x", "line": 1, "text": "x"}], "count": 1, "truncated": False}
        with patch.object(server, "repo_root", return_value=Path("/repo")):
            with patch.object(server, "cache_get", return_value=cached):
                with patch.object(server.subprocess, "run") as run:
                    result = server.search_code({"pattern": "x"})
        self.assertTrue(result["cached"])
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
