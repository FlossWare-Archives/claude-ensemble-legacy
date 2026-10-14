import tempfile
import unittest
from pathlib import Path

from alert_service.alert_service import AlertStore
from learning_service import AutonomousLearningSystem
from memory_service import MemoryStore
from thompson_service import ThompsonState


class FirstRunStorageTests(unittest.TestCase):
    """Verify configured state locations can initialize from a clean filesystem."""

    def test_memory_store_creates_configured_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory_dir = Path(tmp) / "nested" / "memory"
            MemoryStore(memory_dir)
            self.assertTrue(memory_dir.is_dir())

    def test_learning_store_creates_configured_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            learning_dir = Path(tmp) / "nested" / "learning"
            AutonomousLearningSystem(learning_dir)
            self.assertTrue(learning_dir.is_dir())
            self.assertTrue((learning_dir / "autonomous_outcomes").is_dir())
            self.assertTrue((learning_dir / "autonomous_priors").is_dir())

    def test_alert_store_creates_configured_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            alert_dir = Path(tmp) / "nested" / "alerts"
            AlertStore(alert_dir)
            self.assertTrue(alert_dir.is_dir())
            self.assertTrue((alert_dir / "config.json").is_file())

    def test_thompson_state_creates_configured_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "nested" / "learning" / "thompson.json"
            ThompsonState(state_file)
            self.assertTrue(state_file.parent.is_dir())


if __name__ == "__main__":
    unittest.main()
