#!/usr/bin/env bash
# Run GA optimization every four hours and ingest results through Learning/Memory.
set -Eeuo pipefail
umask 077

DEPLOY_ROOT="${ENSEMBLE_INSTALL_ROOT:-$HOME/.FlossWare/claude-ensemble}"
DATA_DIR="$DEPLOY_ROOT/shared/data/ga-tuning"
LOG_DIR="$DEPLOY_ROOT/shared/logs"
SETTINGS_PATH="${CLAUDE_SETTINGS_PATH:-$DEPLOY_ROOT/shared/claude-code/settings.json}"
if [[ -f "$DEPLOY_ROOT/current/ga_tuning/ga_tuner.py" ]]; then
  # Installed execution always follows the stable current symlink, never a
  # mutable development checkout or a release-specific physical path.
  REPO_ROOT="$DEPLOY_ROOT/current"
else
  # Development/test mode remains usable, but persistent state still lives in
  # the isolated deployment root unless explicitly overridden.
  SCRIPT_REAL="$(readlink -f "${BASH_SOURCE[0]}")"
  REPO_ROOT="$(cd "$(dirname "$SCRIPT_REAL")/.." && pwd)"
fi
RESULTS_DIR="${CE_GA_RESULTS_DIR:-$DATA_DIR/results}"
EVOLUTION_LOG="${CE_GA_EVOLUTION_LOG:-$DATA_DIR/parameter_evolution.md}"
LOG_FILE="${CE_GA_LOG_FILE:-$LOG_DIR/ga-tuning-schedule.log}"
WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/ga-tuning.XXXXXX")"
LOCK_FILE="${CE_GA_LOCK_FILE:-$DATA_DIR/ga-tuning.lock}"

cleanup() {
  rm -rf -- "$WORK_DIR"
}
trap cleanup EXIT

mkdir -p "$(dirname "$LOCK_FILE")" "$RESULTS_DIR" "$(dirname "$EVOLUTION_LOG")" "$(dirname "$LOG_FILE")"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  printf '[%s] Another GA tuning run is active; exiting.\n' "$(date +'%Y-%m-%d %H:%M:%S')" | tee -a "$LOG_FILE"
  exit 0
fi

log() {
  printf '[%s] %s\n' "$(date +'%Y-%m-%d %H:%M:%S')" "$*" | tee -a "$LOG_FILE"
}

if [[ ! -f "$SETTINGS_PATH" ]]; then
  log "ERROR: Runtime settings not found: $SETTINGS_PATH"
  log "Set CLAUDE_SETTINGS_PATH to the intended settings file."
  exit 1
fi

log "=== GA Tuning Scheduled Run ==="
log "Repository: $REPO_ROOT"
log "Results: $RESULTS_DIR"
log "Runtime settings: $SETTINGS_PATH"

# GAConfig uses ./results, so run from ga_tuning to keep output in the canonical directory.
log "Step 1: Running local GA optimization..."
if ! (cd "$REPO_ROOT/ga_tuning" && CE_GA_RESULTS_DIR="$RESULTS_DIR" python3 ga_tuner.py) >"$WORK_DIR/ga-output.log" 2>&1; then
  log "ERROR: GA optimization failed; details are appended to $LOG_FILE"
  cat "$WORK_DIR/ga-output.log" >>"$LOG_FILE"
  exit 1
fi
cat "$WORK_DIR/ga-output.log" >>"$LOG_FILE"

log "Step 2: Recording GA candidate artifact through the extractor..."
if ! python3 "$REPO_ROOT/ga_tuning/extract_and_apply_parameters.py" \
  --results-dir "$RESULTS_DIR" \
  --settings-path "$SETTINGS_PATH" \
  --evolution-log "$EVOLUTION_LOG" 2>&1 | tee -a "$LOG_FILE"; then
  log "ERROR: GA candidate ingestion failed"
  exit 1
fi

log "Step 3: Validate runtime settings JSON..."
if ! python3 - "$SETTINGS_PATH" <<'PY' 2>&1 | tee -a "$LOG_FILE"
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
with path.open(encoding="utf-8") as handle:
    settings = json.load(handle)
if not isinstance(settings, dict) or not isinstance(settings.get("env", {}), dict):
    raise SystemExit(f"Invalid Claude settings structure: {path}")
print(f"[Settings] Valid JSON object: {path}")
PY
then
  log "ERROR: Runtime settings validation failed"
  exit 1
fi

log "GA tuning run completed successfully."
