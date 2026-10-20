"""Tests for the GA dashboard's installed and development result paths."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO / "tools" / "ga-tuning-dashboard.py"
SPEC = importlib.util.spec_from_file_location("ga_tuning_dashboard", MODULE_PATH)
DASHBOARD_MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DASHBOARD_MODULE)

RUNTIME_CONFIG_PATH = REPO / "shared" / "runtime_config.py"
RUNTIME_SPEC = importlib.util.spec_from_file_location("ce_runtime_config", RUNTIME_CONFIG_PATH)
RUNTIME_CONFIG = importlib.util.module_from_spec(RUNTIME_SPEC)
RUNTIME_SPEC.loader.exec_module(RUNTIME_CONFIG)


class CEIsolatedRuntimePathTests(unittest.TestCase):
    def test_default_service_state_is_outside_the_normal_claude_profile(self):
        home = Path("/home/ce-test")
        with patch.object(Path, "home", return_value=home), patch.dict(os.environ, {}, clear=True):
            expected = home / ".FlossWare" / "claude-ensemble-state"
            self.assertEqual(RUNTIME_CONFIG.memory_dir(), expected / "data" / "memory")
            self.assertEqual(RUNTIME_CONFIG.learning_dir(), expected / "data" / "learning")
            self.assertEqual(RUNTIME_CONFIG.log_dir(), expected / "logs")
            self.assertEqual(RUNTIME_CONFIG.alert_dir(), expected / "data" / "alerts")

    def test_source_installer_defaults_to_an_isolated_claude_profile(self):
        installer = (REPO / "install.sh").read_text(encoding="utf-8")
        self.assertIn(
            'CLAUDE_HOME="${ENSEMBLE_CLAUDE_CODE_CONFIG_DIR:-$HOME/.FlossWare/claude-ensemble-profile}"',
            installer,
        )
        self.assertNotIn('CLAUDE_HOME="${ENSEMBLE_CLAUDE_CODE_CONFIG_DIR:-$HOME/.claude}"', installer)




class GADashboardDeploymentPathTests(unittest.TestCase):
    def test_installed_dashboard_uses_shared_deployment_results(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / "home"
            root = home / ".FlossWare" / "claude-ensemble"
            (root / "releases" / "0.1").mkdir(parents=True)
            (root / "current").symlink_to("releases/0.1")
            results = root / "shared" / "data" / "ga-tuning" / "results"
            results.mkdir(parents=True)
            with patch.dict(os.environ, {"HOME": str(home), "ENSEMBLE_INSTALL_ROOT": str(root)}, clear=False):
                os.environ.pop("CE_GA_RESULTS_DIR", None)
                dashboard = DASHBOARD_MODULE.GATuningDashboard()
            self.assertEqual(dashboard.results_dir, results)

    def test_explicit_results_directory_overrides_deployment_default(self):
        with tempfile.TemporaryDirectory() as temp:
            results = Path(temp) / "custom-results"
            with patch.dict(os.environ, {"CE_GA_RESULTS_DIR": str(results)}, clear=False):
                dashboard = DASHBOARD_MODULE.GATuningDashboard()
            self.assertEqual(dashboard.results_dir, results)

    def test_development_mode_keeps_repository_local_default(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / "home"
            with patch.dict(os.environ, {"HOME": str(home), "ENSEMBLE_INSTALL_ROOT": str(home / ".FlossWare" / "missing")}, clear=False):
                os.environ.pop("CE_GA_RESULTS_DIR", None)
                dashboard = DASHBOARD_MODULE.GATuningDashboard()
            self.assertEqual(dashboard.results_dir, REPO / "ga_tuning" / "results")


if __name__ == "__main__":
    unittest.main()
