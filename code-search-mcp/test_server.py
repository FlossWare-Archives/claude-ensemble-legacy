#!/usr/bin/env python3
import json
import tempfile
import time
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
            self.assertEqual(command[-3:], ["--", "needle", "."])

    def test_option_shaped_pattern_is_passed_after_option_terminator(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            completed = type("Completed", (), {
                "returncode": 1, "stdout": "", "stderr": ""
            })()
            with patch.object(server, "repo_root", return_value=root):
                with patch.object(server.subprocess, "run", return_value=completed) as run:
                    with patch.object(server, "cache_get", return_value=None), patch.object(server, "cache_put"):
                        server.search_code({"pattern": "--pre", "path": "-directory"})
            command = run.call_args.args[0]
            terminator = command.index("--")
            self.assertEqual(command[terminator + 1:], ["--pre", "-directory"])
            self.assertNotIn("--pre", command[:terminator])
            self.assertNotIn("-directory", command[:terminator])

    def test_cache_key_includes_repository_root(self):
        query = {"pattern": "needle", "path": ".", "max_results": 50}
        self.assertNotEqual(
            server.cache_key(Path("/repo-a"), query),
            server.cache_key(Path("/repo-b"), query),
        )

    def test_fresh_search_is_default_and_does_not_read_or_write_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            completed = type("Completed", (), {
                "returncode": 1, "stdout": "", "stderr": ""
            })()
            with (
                patch.object(server, "repo_root", return_value=root),
                patch.object(server, "cache_get", side_effect=AssertionError("cache read")),
                patch.object(server, "cache_put", side_effect=AssertionError("cache write")),
                patch.object(server.subprocess, "run", return_value=completed) as run,
            ):
                result = server.search_code({"pattern": "newly-added-term"})
            self.assertEqual(result["matches"], [])
            self.assertEqual(result["count"], 0)
            run.assert_called_once()

    def test_cache_directory_creation_failure_is_a_miss(self):
        with patch.object(server, "cache_path", side_effect=PermissionError("mkdir denied")):
            self.assertIsNone(server.cache_get("key"))

    def test_cache_chmod_failure_is_a_miss(self):
        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch.dict(server.os.environ, {"XDG_CACHE_HOME": tmp}),
                patch.object(server.os, "chmod", side_effect=PermissionError("chmod denied")),
            ):
                self.assertIsNone(server.cache_get("key"))

    def test_cache_open_read_failure_is_a_miss(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "cache.jsonl"
            cache.write_text('{"key":"other","timestamp":1,"result":{}}\n')
            with (
                patch.object(server, "cache_path", return_value=cache),
                patch.object(Path, "open", side_effect=PermissionError("read denied")),
            ):
                self.assertIsNone(server.cache_get("key"))

    def test_cache_append_failure_is_nonfatal(self):
        with patch.object(server, "cache_path", side_effect=PermissionError("append denied")):
            server.cache_put("key", {"matches": []})

    def test_ripgrep_failure_still_propagates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            failed = type("Completed", (), {
                "returncode": 2, "stdout": "", "stderr": "invalid regex"
            })()
            with (
                patch.object(server, "repo_root", return_value=root),
                patch.object(server.subprocess, "run", return_value=failed),
            ):
                with self.assertRaisesRegex(RuntimeError, "invalid regex"):
                    server.search_code({"pattern": "["})

    def test_malformed_cache_records_are_misses(self):
        for payload in ("null", "17", "[]", '{"key":"wanted","timestamp":1,"result":null}',
                        '{"key":"wanted","timestamp":"bad","result":{}}'):
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                cache = root / "cache.jsonl"
                cache.write_text(payload + "\n", encoding="utf-8")
                completed = type("Completed", (), {"returncode": 1, "stdout": "", "stderr": ""})()
                with patch.object(server, "repo_root", return_value=root), \
                     patch.object(server, "cache_path", return_value=cache), \
                     patch.object(server.subprocess, "run", return_value=completed) as run:
                    result = server.search_code({"pattern": "needle", "use_cache": True})
                self.assertEqual(result["count"], 0)
                run.assert_called_once()

    def test_cache_malformed_matching_result_is_a_miss(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "cache.jsonl"
            key = server.cache_key(root, {
                "pattern": "needle", "path": ".", "max_results": 50,
                "glob": None, "fixed_string": False, "case_sensitive": True,
            })
            cache.write_text(json.dumps({"key": key, "timestamp": time.time(), "result": []}) + "\n",
                             encoding="utf-8")
            completed = type("Completed", (), {"returncode": 1, "stdout": "", "stderr": ""})()
            with patch.object(server, "repo_root", return_value=root), \
                 patch.object(server, "cache_path", return_value=cache), \
                 patch.object(server.subprocess, "run", return_value=completed) as run:
                result = server.search_code({"pattern": "needle", "use_cache": True})
            self.assertEqual(result["count"], 0)
            run.assert_called_once()

    def test_cache_hit_skips_rg(self):
        cached = {"matches": [{"path": "x", "line": 1, "text": "x"}], "count": 1, "truncated": False}
        with patch.object(server, "repo_root", return_value=Path("/repo")):
            with patch.object(server, "cache_get", return_value=cached):
                with patch.object(server.subprocess, "run") as run:
                    result = server.search_code({"pattern": "x", "use_cache": True})
        self.assertTrue(result["cached"])
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
