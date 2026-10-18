"""Tests for configurable memory cache directory resolution."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from memory_cache_integration import MemoryCacheIntegrator


class MemoryCacheDirectoryTests(unittest.TestCase):
    def test_explicit_argument_takes_precedence_over_environment(self):
        with tempfile.TemporaryDirectory() as explicit_dir, tempfile.TemporaryDirectory() as env_dir:
            with patch.dict(os.environ, {"CE_MEMORY_DIR": env_dir}):
                integrator = MemoryCacheIntegrator(memory_dir=explicit_dir)
            self.assertEqual(integrator.memory_dir, explicit_dir)

    def test_environment_override_is_used(self):
        with tempfile.TemporaryDirectory() as configured_dir:
            with patch.dict(os.environ, {"CE_MEMORY_DIR": configured_dir}):
                integrator = MemoryCacheIntegrator(memory_dir=None)
            self.assertEqual(Path(integrator.memory_dir), Path(configured_dir))


if __name__ == "__main__":
    unittest.main()
