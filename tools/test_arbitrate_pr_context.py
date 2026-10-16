"""Regression tests for PR-head source loading in the arbitration CLI."""

import base64
import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from arbitration.orchestrator import ArbitrationOrchestrator, TaskType
from tools.pr_context import load_pull_request_context


def completed(stdout: str = "", returncode: int = 0, stderr: str = ""):
    return subprocess.CompletedProcess(
        args=[],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


class PullRequestContextTests(unittest.TestCase):
    @patch("tools.pr_context.subprocess.run")
    def test_loads_source_from_exact_head_sha_not_worktree(self, run):
        source = "def secure():\n    return True\n"
        payload = {
            "encoding": "base64",
            "content": base64.b64encode(source.encode()).decode(),
        }
        run.side_effect = [
            completed("diff --git a/app.py b/app.py\n+return True\n"),
            completed(
                json.dumps(
                    {
                        "headRefOid": "abc123",
                        "headRepository": {"name": "repo"},
                        "headRepositoryOwner": {"login": "owner"},
                        "files": [{"path": "app.py", "status": "modified"}],
                    }
                )
            ),
            completed(json.dumps(payload)),
        ]
        orch = ArbitrationOrchestrator(TaskType.CODE_REVIEW, "test")
        load_pull_request_context(orch, Path("/tmp/repo"), "12")

        self.assertEqual(
            orch.context_manager.git_diff,
            "diff --git a/app.py b/app.py\n+return True\n",
        )
        self.assertEqual(
            orch.context_manager.files,
            {"PR_HEAD:abc123:app.py": source},
        )
        args = run.call_args_list[2].args[0]
        self.assertTrue(any("?ref=abc123" in arg for arg in args))
        self.assertIn("repos/owner/repo/contents/app.py?ref=abc123", args)

    @patch("tools.pr_context.subprocess.run")
    def test_refuses_diff_only_when_source_cannot_be_loaded(self, run):
        run.side_effect = [
            completed("diff"),
            completed(
                json.dumps(
                    {
                        "headRefOid": "abc123",
                        "headRepository": {"name": "repo"},
                        "headRepositoryOwner": {"login": "owner"},
                        "files": [{"path": "app.py", "status": "modified"}],
                    }
                )
            ),
            completed('{"message":"Not Found"}'),
        ]
        orch = ArbitrationOrchestrator(TaskType.CODE_REVIEW, "test")

        with self.assertRaises(RuntimeError):
            load_pull_request_context(orch, Path("/tmp/repo"), "12")


if __name__ == "__main__":
    unittest.main()
