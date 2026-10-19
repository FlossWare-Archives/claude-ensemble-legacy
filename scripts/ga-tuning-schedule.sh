#!/usr/bin/env bash
# Backward-compatible entry point for existing cron entries.
# Prefer the deployed release so a stale checkout cannot run stale scheduler code.
set -Eeuo pipefail
DEPLOY_ROOT="${ENSEMBLE_INSTALL_ROOT:-$HOME/.FlossWare/claude-ensemble}"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
DEPLOYED_SCHEDULER="$DEPLOY_ROOT/current/tools/ga-tuning-schedule.sh"
if [[ -f "$DEPLOYED_SCHEDULER" ]]; then
  exec "$DEPLOYED_SCHEDULER" "$@"
fi
# Development/test fallback when no versioned deployment has been staged.
exec "$SCRIPT_DIR/../tools/ga-tuning-schedule.sh" "$@"
