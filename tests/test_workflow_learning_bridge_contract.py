"""Guard the workflow-to-Learning integration boundary.

The old skills called LearningClient.process_outcome directly with heuristic
ratings. All workflow skill outcomes must now pass through the canonical
evidence-gated adapter so Learning owns persistence and Thompson updates.
"""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_SKILLS = (
    "tools/code-pr-review.js",
    "tools/code-pr-review-auto.js",
    "tools/code-doc.js",
    "tools/code-doc-auto.js",
)


class WorkflowLearningBoundaryTests(unittest.TestCase):
    def test_workflow_skills_use_the_shared_evidence_gated_bridge(self) -> None:
        for relative in WORKFLOW_SKILLS:
            source = (ROOT / relative).read_text(encoding="utf-8")
            with self.subTest(skill=relative):
                self.assertIn("recordEvidenceGatedOutcome", source)
                self.assertIn("evidence", source)
                self.assertNotIn("from learning_client import LearningClient", source)
                self.assertNotIn("c.process_outcome(", source)

    def test_review_learning_is_grounded_in_pull_request_artifacts(self) -> None:
        for relative in ("tools/code-pr-review.js", "tools/code-pr-review-auto.js"):
            source = (ROOT / relative).read_text(encoding="utf-8")
            with self.subTest(skill=relative):
                self.assertIn("url", source)
                self.assertIn("review-decision", source) if relative.endswith("code-pr-review.js") else self.assertIn("review-batch-result", source)
                self.assertIn("pr_url", source)

    def test_documentation_learning_is_grounded_in_a_generated_artifact(self) -> None:
        for relative in ("tools/code-doc.js", "tools/code-doc-auto.js"):
            source = (ROOT / relative).read_text(encoding="utf-8")
            with self.subTest(skill=relative):
                self.assertIn("documentation-coverage-report", source)
                self.assertTrue("pr_url" in source or "output_path" in source)

    def test_shared_bridge_invokes_only_the_canonical_adapter(self) -> None:
        source = (ROOT / "shared/learning-outcome-bridge.js").read_text(encoding="utf-8")
        self.assertIn("tools', 'record_workflow_outcome.py", source)
        self.assertIn("stableRunId", source)
        self.assertIn("evidence", source)
        self.assertNotIn("from learning.learning_client import", source)
        self.assertNotIn("ThompsonClient", source)
 
    def test_retired_workflow_completion_hook_is_absent(self) -> None:
        self.assertFalse((ROOT / "hooks/workflow-completion-hook.js").exists())
        settings = (ROOT / "settings.json.default").read_text(encoding="utf-8")
        self.assertNotIn("workflow-completion-hook.js", settings)



if __name__ == "__main__":
    unittest.main()
