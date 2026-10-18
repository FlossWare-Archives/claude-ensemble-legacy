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
