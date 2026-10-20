"""Integration regressions for interrupted Claude Config installs.

These tests use a temporary HOME and dedicated CE profile. They never read or
mutate the developer's real Claude profile or running services.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "tools" / "claude-config" / "install.sh"
SESSION_SOURCE = ROOT / "hooks" / "session-end-memory-capture.js"
MEMORY_SOURCE = ROOT / "hooks" / "memory-search-on-prompt.js"


@unittest.skipUnless(shutil.which("node"), "Node.js is required by the installer")
class ManagedInstallRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="ce-install-recovery-")
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.profile = self.home / ".FlossWare" / "claude-ensemble" / "shared" / "claude-code"
        self.hooks = self.profile / "hooks"
        self.hooks.mkdir(parents=True)
        self.env = os.environ.copy()
        self.env["HOME"] = str(self.home)
        self.env["ENSEMBLE_CLAUDE_CODE_CONFIG_DIR"] = str(self.profile)
        self.env.pop("CLAUDE_CONFIG_DIR", None)
        self.env.pop("FLOSSWARE_CLAUDE_CONFIG_STATE_DIR", None)

    def run_install(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(INSTALLER), "--non-interactive"],
            cwd=ROOT,
            env=self.env,
            text=True,
            capture_output=True,
            timeout=20,
            check=False,
        )

    def write_settings_with_session_hook(self) -> None:
        settings = {
            "hooks": {
                "SessionEnd": [
                    {
                        "matcher": "exit",
                        "hooks": [
                            {
                                "type": "command",
                                "command": str(self.hooks / SESSION_SOURCE.name),
                            }
                        ],
                    }
                ]
            }
        }
        (self.profile / "settings.json").write_text(
            json.dumps(settings, indent=2) + "\n", encoding="utf-8"
        )

    def test_canonical_session_hook_without_manifest_is_recovered(self) -> None:
        self.write_settings_with_session_hook()
        shutil.copy2(SESSION_SOURCE, self.hooks / SESSION_SOURCE.name)
        # Simulate an interrupted/legacy install: hook and settings exist,
        # but the ownership manifest was never written.
        result = self.run_install()
        self.assertEqual(
            0,
            result.returncode,
            msg=f"installer failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )
        manifest = self.profile / ".flossware-claude-config" / "manifest.json"
        self.assertTrue(manifest.is_file(), "successful recovery must recreate the manifest")
        data = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertEqual("flossware-claude-config", data.get("product"))
        self.assertTrue(data.get("files"), "manifest must contain managed hook entries")
        settings = json.loads((self.profile / "settings.json").read_text(encoding="utf-8"))
        registered = settings.get("hooks", {}).get("SessionEnd", [])
        commands = [
            hook.get("command")
            for group in registered if isinstance(group, dict)
            for hook in group.get("hooks", []) if isinstance(hook, dict)
        ]
        self.assertEqual(1, sum(str(command).endswith(SESSION_SOURCE.name) for command in commands))
        self.assertTrue((self.profile / "settings.json").is_file())

    def test_unknown_session_hook_without_manifest_remains_protected(self) -> None:
        self.write_settings_with_session_hook()
        existing = self.hooks / SESSION_SOURCE.name
        existing.write_text("// locally customized hook; not canonical\n", encoding="utf-8")
        before = (self.profile / "settings.json").read_bytes()
        result = self.run_install()
        self.assertNotEqual(0, result.returncode)
        self.assertIn("not provably FlossWare-managed", result.stderr)
        self.assertEqual(before, (self.profile / "settings.json").read_bytes())
        self.assertEqual(
            "// locally customized hook; not canonical\n",
            existing.read_text(encoding="utf-8"),
            "recovery must not overwrite an unknown hook",
        )
        self.assertFalse(
            (self.profile / ".flossware-claude-config" / "manifest.json").exists(),
            "failed preflight must not fabricate ownership metadata",
        )

    def test_canonical_memory_hook_without_manifest_is_recovered(self) -> None:
        self.write_settings_with_session_hook()
        shutil.copy2(SESSION_SOURCE, self.hooks / SESSION_SOURCE.name)
        shutil.copy2(MEMORY_SOURCE, self.hooks / MEMORY_SOURCE.name)
        # Include the canonical memory registration to exercise both recovery
        # guards against the exact source files shipped in this checkout.
        settings_path = self.profile / "settings.json"
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        settings["hooks"]["UserPromptSubmit"] = [
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": str(self.hooks / MEMORY_SOURCE.name),
                    }
                ]
            }
        ]
        settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
        result = self.run_install()
        self.assertEqual(
            0,
            result.returncode,
            msg=f"installer failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )
        manifest = json.loads(
            (self.profile / ".flossware-claude-config" / "manifest.json").read_text(encoding="utf-8")
        )
        self.assertTrue(manifest.get("files"))
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        prompt = settings.get("hooks", {}).get("UserPromptSubmit", [])
        prompt_commands = [
            hook.get("command")
            for group in prompt if isinstance(group, dict)
            for hook in group.get("hooks", []) if isinstance(hook, dict)
        ]
        self.assertEqual(1, sum(str(command).endswith(MEMORY_SOURCE.name) for command in prompt_commands))


if __name__ == "__main__":
    unittest.main()
