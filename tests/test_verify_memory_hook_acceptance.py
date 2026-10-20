"""Focused regression tests for scripts/verify-memory-hook-acceptance.cjs.

Verifies the operational acceptance script against mock Memory HTTP service fixtures
covering all exit status scenarios, working directory independence, and content matching.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
ACCEPTANCE_SCRIPT = ROOT / "scripts" / "verify-memory-hook-acceptance.cjs"


class MockMemoryHandler(BaseHTTPRequestHandler):
    mode = "both"

    def log_message(self, *_args: object) -> None:
        pass

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            body = b'{"ok": true, "service": "memory"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode()) if length else {}
        except (ValueError, json.JSONDecodeError):
            payload = {}

        if self.path == "/memory/search":
            source = payload.get("source")
            mode = type(self).mode

            if mode == "both":
                if source == "memory":
                    results = [{"file": "decision.md", "source": "memory", "content": "Seeded decision memory record"}]
                else:
                    results = [{"file": "claude-code-arch.md", "source": "claude-code", "content": "Seeded Claude Code knowledge fact"}]
            elif mode == "memory_only":
                if source == "memory":
                    results = [{"file": "decision.md", "source": "memory", "content": "Seeded decision memory record"}]
                else:
                    results = []
            elif mode == "knowledge_only":
                if source == "memory":
                    results = []
                else:
                    results = [{"file": "claude-code-arch.md", "source": "claude-code", "content": "Seeded Claude Code knowledge fact"}]
            elif mode == "generic_headings_only":
                if source == "memory":
                    results = [{"file": "decision.md", "source": "memory", "content": "Unrelated dummy text"}]
                else:
                    results = [{"file": "claude-code-arch.md", "source": "claude-code", "content": "Other dummy text"}]
            elif mode == "empty":
                results = []
            elif mode == "malformed_schema":
                # Returns 200 OK with ok=true but results as invalid non-array type
                results = "not_an_array"
            else:
                results = []

            body = json.dumps({"ok": True, "results": results}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()


class VerifyMemoryHookAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if NODE is None:
            raise unittest.SkipTest("Node.js is required")
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), MockMemoryHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.memory_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def run_acceptance_script(
        self,
        cwd: str | Path | None = None,
        url: str | None = None,
        prompt: str = "architecture decision",
        memory_text: str = "decision",
        knowledge_text: str = "Claude Code",
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["FLOSSWARE_MEMORY_URL"] = url or self.memory_url
        env["EXPECTED_PROMPT"] = prompt
        env["EXPECTED_MEMORY_TEXT"] = memory_text
        env["EXPECTED_KNOWLEDGE_TEXT"] = knowledge_text

        return subprocess.run(
            [NODE, str(ACCEPTANCE_SCRIPT), prompt, memory_text, knowledge_text],
            cwd=cwd or ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

    def test_both_expected_records_present_returns_exit_0(self) -> None:
        MockMemoryHandler.mode = "both"
        result = self.run_acceptance_script()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("ACCEPTANCE SUCCESS", result.stdout)

    def test_memory_present_knowledge_missing_returns_exit_3(self) -> None:
        MockMemoryHandler.mode = "memory_only"
        result = self.run_acceptance_script()
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn("ACCEPTANCE FAILURE", result.stderr)

    def test_knowledge_present_memory_missing_returns_exit_3(self) -> None:
        MockMemoryHandler.mode = "knowledge_only"
        result = self.run_acceptance_script()
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn("ACCEPTANCE FAILURE", result.stderr)

    def test_neither_expected_record_present_returns_exit_3(self) -> None:
        MockMemoryHandler.mode = "empty"
        result = self.run_acceptance_script()
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn("ACCEPTANCE FAILURE", result.stderr)

    def test_generic_headings_without_expected_record_content_returns_exit_3(self) -> None:
        MockMemoryHandler.mode = "generic_headings_only"
        result = self.run_acceptance_script(memory_text="specific_decision_key", knowledge_text="specific_knowledge_key")
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn("ACCEPTANCE FAILURE", result.stderr)

    def test_memory_service_unavailable_returns_exit_1(self) -> None:
        result = self.run_acceptance_script(url="http://127.0.0.1:1")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("unavailable", result.stderr)

    def test_malformed_hook_output_returns_exit_2(self) -> None:
        # Run acceptance script from a temporary directory missing the hook script
        with tempfile.TemporaryDirectory(prefix="ce-no-hook-") as temp_dir:
            temp_script = Path(temp_dir) / "verify-memory-hook-acceptance.cjs"
            shutil.copy(ACCEPTANCE_SCRIPT, temp_script)
            env = os.environ.copy()
            env["FLOSSWARE_MEMORY_URL"] = self.memory_url
            result = subprocess.run(
                [NODE, str(temp_script), "prompt", "memory", "knowledge"],
                cwd=temp_dir,
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertIn("ACCEPTANCE ERROR (Exit 2)", result.stderr)

    def test_executed_from_working_directory_outside_repository_root(self) -> None:
        MockMemoryHandler.mode = "both"
        with tempfile.TemporaryDirectory(prefix="ce-outside-cwd-") as outside_dir:
            result = self.run_acceptance_script(cwd=outside_dir)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("ACCEPTANCE SUCCESS", result.stdout)


if __name__ == "__main__":
    unittest.main()
