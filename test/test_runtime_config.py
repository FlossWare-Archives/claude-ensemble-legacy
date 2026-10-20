import os
import tempfile
import unittest
from pathlib import Path

from shared import runtime_config


class RuntimeConfigTests(unittest.TestCase):
    def test_environment_overrides(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            values = {
                "ENSEMBLE_MEMORY_DIR": str(root / "memory"),
                "ENSEMBLE_LOG_DIR": str(root / "logs"),
                "ENSEMBLE_RUNTIME_DIR": str(root / "runtime"),
                "ENSEMBLE_LEARNING_DIR": str(root / "learning"),
                "ENSEMBLE_ALERT_DIR": str(root / "alerts"),
                "ENSEMBLE_REPO_ROOT": str(root / "repo"),
                "ENSEMBLE_TEST_SOCKET": str(root / "socket"),
                "ENSEMBLE_MEMORY_SOCKET": str(root / "memory.sock"),
                "ENSEMBLE_THOMPSON_STATE_FILE": str(root / "thompson-state.json"),
            }
            previous = {key: os.environ.get(key) for key in values}
            try:
                os.environ.update(values)
                self.assertEqual(runtime_config.memory_dir(), root / "memory")
                self.assertEqual(runtime_config.log_dir(), root / "logs")
                self.assertEqual(runtime_config.runtime_dir(), root / "runtime")
                self.assertEqual(runtime_config.learning_dir(), root / "learning")
                self.assertEqual(runtime_config.alert_dir(), root / "alerts")
                self.assertEqual(runtime_config.repo_root(), root / "repo")
                self.assertEqual(
                    runtime_config.socket_path("ENSEMBLE_TEST_SOCKET", "/tmp/legacy.sock"),
                    root / "socket",
                )
                self.assertEqual(
                    runtime_config.socket_path("ENSEMBLE_MEMORY_SOCKET", "/tmp/memory.sock"),
                    root / "memory.sock",
                )
                self.assertEqual(
                    runtime_config.thompson_state_file(),
                    root / "thompson-state.json",
                )
            finally:
                for key, value in previous.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

    def test_learning_default_uses_isolated_ce_state(self):
        os.environ.pop("ENSEMBLE_LEARNING_DIR", None)
        self.assertEqual(
            runtime_config.learning_dir(),
            Path.home() / ".FlossWare" / "claude-ensemble-state" / "data" / "learning",
        )

    def test_thompson_state_default_follows_learning_dir(self):
        os.environ.pop("ENSEMBLE_THOMPSON_STATE_FILE", None)
        os.environ.pop("ENSEMBLE_LEARNING_DIR", None)
        self.assertEqual(
            runtime_config.thompson_state_file(),
            Path.home() / ".FlossWare" / "claude-ensemble-state" / "data" / "learning"
            / "thompson-sampling-state.json",
        )

    def test_memory_socket_default_uses_runtime_dir(self):
        os.environ.pop("ENSEMBLE_MEMORY_SOCKET", None)
        os.environ["ENSEMBLE_RUNTIME_DIR"] = "/tmp/claude-ensemble-test-runtime"
        try:
            self.assertEqual(
                runtime_config.socket_path(
                    "ENSEMBLE_MEMORY_SOCKET",
                    str(runtime_config.runtime_dir() / "memory.sock"),
                ),
                Path("/tmp/claude-ensemble-test-runtime/memory.sock"),
            )
        finally:
            os.environ.pop("ENSEMBLE_RUNTIME_DIR", None)

    def test_socket_legacy_default_is_preserved(self):
        os.environ.pop("ENSEMBLE_TEST_SOCKET", None)
        self.assertEqual(
            runtime_config.socket_path("ENSEMBLE_TEST_SOCKET", "/tmp/legacy.sock"),
            Path("/tmp/legacy.sock"),
        )


if __name__ == "__main__":
    unittest.main()
