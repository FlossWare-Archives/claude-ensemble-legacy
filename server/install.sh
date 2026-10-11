#!/usr/bin/env bash
# Install the canonical Claude Ensemble REST gateway as a systemd user service.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_NAME="claude-ensemble.service"
SERVICE_TEMPLATE="$REPO_ROOT/server/claude-ensemble.service.template"
SYSTEMCTL_BIN="${SYSTEMCTL_BIN:-systemctl}"
SYSTEMD_DIR="${HOME}/.config/systemd/user"
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
"$SYSTEMCTL_BIN" --user start "$SERVICE_NAME"

if "$SYSTEMCTL_BIN" --user is-active --quiet "$SERVICE_NAME"; then
  echo "REST gateway installed and running: $SERVICE_NAME"
else
  echo "REST gateway failed to start. Check: journalctl --user -u $SERVICE_NAME" >&2
  exit 1
fi
