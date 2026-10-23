import unittest

from knowledge_service import provenance


class KnowledgeProvenanceTest(unittest.TestCase):
    def test_stable_ids_are_order_independent(self):
        self.assertEqual(
            provenance.stable_id("node", {"a": 1, "b": 2}),
            provenance.stable_id("node", {"b": 2, "a": 1}),
        )

    def test_claim_is_candidate_and_evidence_backed(self):
        evidence = {"source_path": "/project/CLAUDE.md", "content_sha256": "abc"}
        item = provenance.knowledge_record("Use Java", "preference", evidence, scope="/project")
        self.assertEqual(item["status"], "candidate")
        self.assertEqual(item["evidence"], evidence)

    def test_claim_requires_evidence(self):
        with self.assertRaises(ValueError):
            provenance.knowledge_record("Claim", "fact", {})


if __name__ == "__main__":
    unittest.main()
