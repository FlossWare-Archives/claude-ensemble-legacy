import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "lib" / "rag_config.py"


class RagConfigCliTests(unittest.TestCase):
    def run_cli(self, home, profile=None, *args):
        env = dict(os.environ)
        env["HOME"] = str(home)
        env.pop("ENSEMBLE_CLAUDE_CODE_CONFIG_DIR", None)
        env.pop("CLAUDE_CONFIG_DIR", None)
        if profile is not None:
            env["ENSEMBLE_CLAUDE_CODE_CONFIG_DIR"] = str(profile)
        return subprocess.run(
            ["python3", str(SCRIPT), *args],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_show_defaults_and_atomic_private_update(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary) / "home"
            profile = home / "ce-profile"
            profile.mkdir(parents=True)
            shown = self.run_cli(home, profile, "show")
            self.assertEqual(0, shown.returncode, shown.stderr)
            self.assertTrue(json.loads(shown.stdout)["memory"]["enabled"])

            updated = self.run_cli(
                home, profile, "set", "memory.enabled=false", "graph.max_nodes=4"
            )
            self.assertEqual(0, updated.returncode, updated.stderr)
            config_path = profile / ".flossware-claude-config" / "rag.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertFalse(config["memory"]["enabled"])
            self.assertEqual(4, config["graph"]["max_nodes"])
            self.assertEqual(0o600, config_path.stat().st_mode & 0o777)
            self.assertEqual(0o700, config_path.parent.stat().st_mode & 0o777)

    def test_invalid_value_does_not_overwrite_existing_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary) / "home"
            profile = home / "ce-profile"
            profile.mkdir(parents=True)
            self.assertEqual(0, self.run_cli(home, profile, "set", "memory.enabled=false").returncode)
            config_path = profile / ".flossware-claude-config" / "rag.json"
            before = config_path.read_bytes()
            failed = self.run_cli(home, profile, "set", "graph.max_nodes=99")
            self.assertNotEqual(0, failed.returncode)
            self.assertIn("graph.max_nodes", failed.stderr)
            self.assertEqual(before, config_path.read_bytes())

    def test_refuses_implicit_or_normal_claude_profile(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary) / "home"
            (home / ".claude").mkdir(parents=True)
            missing = self.run_cli(home, None, "reset")
            self.assertNotEqual(0, missing.returncode)
            self.assertIn("refusing to use ~/.claude", missing.stderr)
            normal = self.run_cli(home, home / ".claude", "reset")
            self.assertNotEqual(0, normal.returncode)
            self.assertIn("resolves inside ~/.claude", normal.stderr)
            self.assertFalse((home / ".claude" / ".flossware-claude-config").exists())


if __name__ == "__main__":
    unittest.main()
