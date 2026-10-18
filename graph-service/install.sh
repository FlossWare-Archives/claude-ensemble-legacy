#!/usr/bin/env bash
# Install Claude Ensemble Graph Service as a systemd user service.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_NAME="claude-graph.service"
SERVICE_TEMPLATE="$REPO_ROOT/graph-service/claude-graph.service.template"
SYSTEMCTL_BIN="${SYSTEMCTL_BIN:-systemctl}"
SYSTEMD_DIR="${ENSEMBLE_USER_UNIT_DIR:-${HOME}/.config/systemd/user}"
SERVICE_FILE="$SYSTEMD_DIR/$SERVICE_NAME"

if [[ ! -f "$SERVICE_TEMPLATE" ]]; then
  echo "Error: service template not found: $SERVICE_TEMPLATE" >&2
  exit 1
fi

mkdir -p "$SYSTEMD_DIR"
sed "s|%REPO_PATH%|$REPO_ROOT|g" "$SERVICE_TEMPLATE" > "$SERVICE_FILE"
chmod 0644 "$SERVICE_FILE"

"$SYSTEMCTL_BIN" --user daemon-reload
"$SYSTEMCTL_BIN" --user enable "$SERVICE_NAME"
if [[ "${ENSEMBLE_DEFER_SERVICE_START:-0}" == "1" ]]; then
  echo "Service installed and enabled; start deferred to the top-level installer: $SERVICE_NAME"
  exit 0
fi
"$SYSTEMCTL_BIN" --user start "$SERVICE_NAME"

if "$SYSTEMCTL_BIN" --user is-active --quiet "$SERVICE_NAME"; then
  echo "Graph service installed and running: $SERVICE_NAME"
else
  echo "Graph service failed to start. Check: journalctl --user -u $SERVICE_NAME" >&2
  exit 1
fi
