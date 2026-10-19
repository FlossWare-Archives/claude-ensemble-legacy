#!/usr/bin/env bash
# Backward-compatible entry point for existing cron entries.
#
# Keep scheduled GA artifacts, evolution history, locks, logs, and settings
# under the standalone deployment's shared state. The canonical scheduler
# selects the immutable release through ~/.FlossWare/claude-ensemble/current.
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
exec "$SCRIPT_DIR/../tools/ga-tuning-schedule.sh" "$@"
