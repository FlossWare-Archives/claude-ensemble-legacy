import importlib.util
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "knowledge-service" / "provenance.py"
SPEC = importlib.util.spec_from_file_location("knowledge_provenance", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class KnowledgeProvenanceTest(unittest.TestCase):
    def test_stable_ids_are_order_independent(self):
        self.assertEqual(
            MODULE.stable_id("node", {"a": 1, "b": 2}),
            MODULE.stable_id("node", {"b": 2, "a": 1}),
        )

    def test_claim_is_candidate_and_evidence_backed(self):
        evidence = {"source_path": "/project/CLAUDE.md", "content_sha256": "abc"}
        item = MODULE.knowledge_record("Use Java", "preference", evidence, scope="/project")
        self.assertEqual(item["status"], "candidate")
        self.assertEqual(item["evidence"], evidence)

    def test_claim_requires_evidence(self):
        with self.assertRaises(ValueError):
            MODULE.knowledge_record("Claim", "fact", {})


if __name__ == "__main__":
    unittest.main()
