"""Contract tests for deployment-owned scheduled GA state."""
from pathlib import Path
import unittest

REPO = Path(__file__).resolve().parents[1]


class GATuningDeploymentPathTests(unittest.TestCase):
    def test_scheduler_keeps_persistent_state_out_of_checkout_and_default_claude_profile(self):
        script = (REPO / "tools" / "ga-tuning-schedule.sh").read_text(encoding="utf-8")
        self.assertIn('DEPLOY_ROOT="${ENSEMBLE_INSTALL_ROOT:-$HOME/.FlossWare/claude-ensemble}"', script)
        self.assertIn('DATA_DIR="$DEPLOY_ROOT/shared/data/ga-tuning"', script)
        self.assertIn('LOG_DIR="$DEPLOY_ROOT/shared/logs"', script)
        self.assertIn('SETTINGS_PATH="${CLAUDE_SETTINGS_PATH:-$DEPLOY_ROOT/shared/claude-code/settings.json}"', script)
        self.assertIn('CE_GA_RESULTS_DIR="$RESULTS_DIR" python3 ga_tuner.py', script)
        self.assertNotIn('$HOME/.claude/settings.json', script)

    def test_optimizer_honors_scheduler_output_directory(self):
        source = (REPO / "ga_tuning" / "ga_tuner.py").read_text(encoding="utf-8")
        self.assertIn('config.output_dir = os.environ.get("CE_GA_RESULTS_DIR", config.output_dir)', source)

    def test_legacy_scheduler_prefers_deployed_release_over_stale_checkout(self):
        script = (REPO / "scripts" / "ga-tuning-schedule.sh").read_text(encoding="utf-8")
        self.assertIn('DEPLOYED_SCHEDULER="$DEPLOY_ROOT/current/tools/ga-tuning-schedule.sh"', script)
        self.assertIn('exec "$DEPLOYED_SCHEDULER" "$@"', script)
        self.assertIn('exec "$SCRIPT_DIR/../tools/ga-tuning-schedule.sh" "$@"', script)
        self.assertNotIn('$HOME/.claude', script)
        self.assertNotIn('RH_TOOLS_ROOT', script)


if __name__ == "__main__":
    unittest.main()
