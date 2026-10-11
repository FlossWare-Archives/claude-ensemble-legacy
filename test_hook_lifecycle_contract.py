"""Regression tests for the Claude Ensemble hook lifecycle boundaries."""
from pathlib import Path
import json
import subprocess
import unittest


ROOT = Path(__file__).resolve().parent


class HookLifecycleContractTests(unittest.TestCase):
    def read(self, relative_path: str) -> str:
        return (ROOT / relative_path).read_text(encoding="utf-8")

    def run_node(self, script: str) -> dict:
        completed = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return json.loads(completed.stdout)

    def test_rag_worker_count_defaults_overrides_and_invalid_fallback(self):
        hook = self.read("hooks/memory-rag-search.js")
        start = hook.index("function resolveWorkerCount(value) {")
        opening = hook.index("{", start)
        depth = 0
        end = None
        for index in range(opening, len(hook)):
            if hook[index] == "{":
                depth += 1
            elif hook[index] == "}":
                depth -= 1
                if depth == 0:
                    end = index + 1
                    break
        self.assertIsNotNone(end, "worker-count resolver must have a complete body")
        function_source = hook[start:end]
        script = (
            "const source = " + json.dumps(function_source) + ";"
            "const diagnostics = [];"
            "const resolve = new Function('DEFAULT_WORKER_COUNT', "
            "'MAX_WORKER_COUNT', 'log', 'return (' + source + ');')"
            "(4, 4, message => diagnostics.push(message));"
            "const results = [resolve(undefined), resolve(null), resolve(1), "
            "resolve('2'), resolve(4), resolve(0), resolve(5), "
            "resolve(1.5), resolve(true), resolve('invalid')];"
            "process.stdout.write(JSON.stringify({results, diagnostics}));"
        )
        result = self.run_node(script)
        self.assertEqual(result["results"], [4, 4, 1, 2, 4, 4, 4, 4, 4, 4])
        self.assertEqual(len(result["diagnostics"]), 5)
        self.assertIn("valid range: 1-4", result["diagnostics"][0])

    def test_prompt_hook_is_read_only_context_retrieval(self):
        hook = self.read("hooks/memory-search-on-prompt.js")
        self.assertIn("/memory/search", hook)
        self.assertIn("UserPromptSubmit", hook)
        self.assertNotIn("storeLearnings(", hook)
        self.assertNotIn("post_task_analyzer", hook)
        self.assertNotIn("thompson", hook.lower())

    def test_legacy_post_task_hook_is_inert_at_runtime(self):
        result = self.run_node(
            "import hook from './hooks/post-task-analysis.js'; "
            "const result = await hook.execute(); "
            "process.stdout.write(JSON.stringify(result));"
        )
        self.assertEqual(result["skipped"], True)
        self.assertEqual(
            result["reason"], "disabled_until_learning_service_delegation"
        )

    def test_legacy_post_task_hook_has_no_executable_learning_side_effects(self):
        hook = self.read("hooks/post-task-analysis.js")
        self.assertIn('event: "WorkflowComplete"', hook)
        self.assertIn("enabled: false", hook)
        self.assertNotIn('event: "UserPromptSubmit"', hook)
        for forbidden in (
            "require('../learning/post_task_analyzer",
            'require("../learning/post_task_analyzer',
            "child_process",
            "spawn(",
            "execFile(",
            "fork(",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, hook)

    def test_legacy_workflow_hook_is_inert_at_runtime(self):
        result = self.run_node(
            "import hook from './hooks/post-workflow-learning.js'; "
            "const result = await hook.onWorkflowComplete(); "
            "process.stdout.write(JSON.stringify(result));"
        )
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(
            result["reason"], "disabled_until_learning_service_delegation"
        )

    def test_legacy_workflow_hook_cannot_write_learning_storage_directly(self):
        hook = self.read("hooks/post-workflow-learning.js")
        self.assertIn("disabled_until_learning_service_delegation", hook)
        self.assertNotIn("storeLearnings(", hook)
        self.assertNotIn("postgres-adapter", hook)

    def test_lifecycle_contract_requires_idempotency_and_evidence_gating(self):
        contract = self.read("docs/CLAUDE_CONTEXT_HOOK_LIFECYCLE.md")
        required_contracts = (
            "stable event/run identifier",
            "must be idempotent",
            "Missing or invalid outcome evidence",
            "Knowledge promotion is a separate explicit operation",
            "must remain disabled",
        )
        for requirement in required_contracts:
            with self.subTest(requirement=requirement):
                self.assertIn(requirement, contract)

    def test_session_end_scripts_are_not_claimed_as_exactly_once(self):
        contract = self.read("docs/CLAUDE_CONTEXT_HOOK_LIFECYCLE.md")
        self.assertIn("no stable per-event idempotency key", contract)
        self.assertIn("Do not register both session-end scripts", contract)


if __name__ == "__main__":
    unittest.main()
