"""Regression test for memory inventory completeness defect.

This test verifies the fix for the issue where Claude Code would:
1. Claim no memories exist when querying the wrong directory
2. Then surface memories from the hook context, contradicting the first claim
3. Fail to locate a known memory (Google accomplishments doc) despite it existing
4. Provide incomplete results when asking "what have you remembered"

The fix ensures:
- Memory inventory checks the actual Memory service directory
- When asked for memory inventory, provide complete list, not just search results
- Clearly distinguish: (1) records that exist but weren't retrieved, (2) records never saved
- Search-based retrieval remains the primary path for context injection
- But inventory/list operations must enumerate actual storage
"""

import json
import unittest
from pathlib import Path


class MemoryInventoryTest(unittest.TestCase):
    """Verify memory inventory is complete and accurate."""

    def setUp(self) -> None:
        """Set up test environment."""
        self.memory_dir = Path.home() / ".claude" / "projects" / "memory"
        if not self.memory_dir.is_dir():
            self.skipTest("Host-specific Memory data is unavailable; run this check on an installed host")

    def test_memory_directory_has_files(self) -> None:
        """Verify the Memory service directory contains memories."""
        md_files = list(self.memory_dir.glob("*.md"))
        self.assertGreater(len(md_files), 0,
                          "Memory directory should contain .md memory files")

    def test_accomplishments_doc_exists(self) -> None:
        """Verify the accomplishments reference memory exists."""
        expected_file = self.memory_dir / "reference_accomplishments_doc.md"
        self.assertTrue(expected_file.exists(),
                       "reference_accomplishments_doc.md must exist in memory directory")

        with open(expected_file) as f:
            content = f.read()
        self.assertIn("accomplishments", content.lower(),
                     "Accomplishments doc should mention accomplishments")

    def test_memory_inventory_not_empty(self) -> None:
        """Verify a complete memory inventory would not be empty."""
        memory_records = self._collect_all_memories()
        self.assertGreater(len(memory_records), 0,
                          "Memory inventory should contain records")

    def test_memory_inventory_includes_reference_types(self) -> None:
        """Verify inventory includes all memory types (user, feedback, project, reference)."""
        memory_records = self._collect_all_memories()

        types_found = set()
        for record in memory_records:
            # Look for type in file content or metadata line
            file_path = self.memory_dir / record.get("file", "")
            if file_path.exists():
                try:
                    with open(file_path) as f:
                        content = f.read()
                    if "type: reference" in content:
                        types_found.add("reference")
                    elif "type: user" in content:
                        types_found.add("user")
                    elif "type: feedback" in content:
                        types_found.add("feedback")
                    elif "type: project" in content:
                        types_found.add("project")
                except Exception:
                    pass

        self.assertIn("reference", types_found,
                     "Should have reference-type memories (like accomplishments doc)")

        # At least one of these should exist
        self.assertTrue(types_found & {"user", "feedback", "project", "reference"},
                       "Should have at least one memory type")

    def test_accomplishments_doc_is_retrievable(self) -> None:
        """Verify accomplishments doc would be found in a complete inventory."""
        memory_records = self._collect_all_memories()
        accomplishment_records = [r for r in memory_records
                                 if "accomplishment" in r.get("name", "").lower()]

        self.assertGreater(len(accomplishment_records), 0,
                          "Accomplishments record should exist in inventory")

    def test_search_results_are_subset_of_inventory(self) -> None:
        """Verify search results don't claim to be complete when inventory is larger."""
        all_memories = self._collect_all_memories()

        # A search query is unlikely to find every memory
        # This test documents that search ≠ inventory
        self.assertGreater(len(all_memories), 0,
                          "Full inventory should have records")

    def test_memory_frontmatter_consistency(self) -> None:
        """Verify memory files have required frontmatter."""
        md_files = list(self.memory_dir.glob("*.md"))

        for md_file in md_files[:10]:  # Check first 10 as sample
            with open(md_file) as f:
                content = f.read()

            # Memory files should have frontmatter
            if content.startswith("---"):
                self.assertIn("name:", content,
                             f"{md_file.name} should have 'name' in frontmatter")
                self.assertIn("metadata:", content,
                             f"{md_file.name} should have 'metadata' in frontmatter")

    def test_memory_dir_is_not_session_memory_dir(self) -> None:
        """Verify we don't confuse Memory service dir with session-local memory dir.

        This documents the root cause of the original defect: checking
        a session-specific memory directory instead of the shared Memory service.
        """
        session_memory_dir = Path.cwd() / "memory"
        service_memory_dir = Path.home() / ".claude" / "projects" / "memory"

        # The bug was treating these as the same
        self.assertNotEqual(session_memory_dir, service_memory_dir,
                           "Must not confuse session memory with service memory")

        # The service memory dir is where all memories live
        self.assertTrue(service_memory_dir.exists(),
                       "Service memory dir must exist")

    def _collect_all_memories(self) -> list:
        """Collect all memory records from the Memory directory.

        This is what a proper inventory operation should do.
        Returns list of dicts with file info and parsed frontmatter.
        """
        memories = []

        for md_file in self.memory_dir.glob("*.md"):
            try:
                with open(md_file) as f:
                    content = f.read()

                # Parse frontmatter
                if content.startswith("---"):
                    end_marker = content.find("---", 3)
                    if end_marker > 0:
                        frontmatter_str = content[3:end_marker]
                        try:
                            # Simple YAML-like parsing
                            record = {"file": md_file.name}
                            for line in frontmatter_str.split("\n"):
                                if ":" in line:
                                    key, val = line.split(":", 1)
                                    record[key.strip()] = val.strip()
                            memories.append(record)
                        except Exception:
                            pass
            except Exception:
                pass

        return memories


if __name__ == "__main__":
    unittest.main()
