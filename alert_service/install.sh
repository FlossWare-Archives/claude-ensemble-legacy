#!/bin/bash
# Install Claude Ensemble Alert Service

set -e

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SERVICE_NAME="claude-alert"
SERVICE_TEMPLATE="$REPO_ROOT/alert_service/${SERVICE_NAME}.service.template"
SERVICE_DIR="${ENSEMBLE_USER_UNIT_DIR:-$HOME/.config/systemd/user}"
SYSTEMCTL_BIN="${SYSTEMCTL_BIN:-systemctl}"

if [ ! -f "$SERVICE_TEMPLATE" ]; then
    echo "Error: Service template not found at $SERVICE_TEMPLATE"
    exit 1
fi

mkdir -p "$SERVICE_DIR"

echo "Installing $SERVICE_NAME systemd user service..."
sed "s|%REPO_PATH%|$REPO_ROOT|g" "$SERVICE_TEMPLATE" > "$SERVICE_DIR/${SERVICE_NAME}.service"
chmod 644 "$SERVICE_DIR/${SERVICE_NAME}.service"

echo "Reloading systemd configuration..."
"$SYSTEMCTL_BIN" --user daemon-reload

echo "Enabling $SERVICE_NAME service (auto-start)..."
"$SYSTEMCTL_BIN" --user enable "${SERVICE_NAME}.service"
if [ "${ENSEMBLE_DEFER_SERVICE_START:-0}" = "1" ]; then
    echo "$SERVICE_NAME installed and enabled; start deferred to the top-level installer."
    exit 0
fi

echo "Starting $SERVICE_NAME service..."
"$SYSTEMCTL_BIN" --user start "${SERVICE_NAME}.service"

echo ""
echo "Installation complete!"
echo ""
echo "Service status:"
"$SYSTEMCTL_BIN" --user status "${SERVICE_NAME}.service" --no-pager || true

echo ""
echo "To check the service:"
echo "  $SYSTEMCTL_BIN --user status $SERVICE_NAME"
echo ""
echo "To view logs:"
echo "  journalctl --user-unit ${SERVICE_NAME}.service -f"
echo ""
echo "To stop the service:"
echo "  $SYSTEMCTL_BIN --user stop $SERVICE_NAME"
echo ""
echo "To disable auto-start:"
echo "  $SYSTEMCTL_BIN --user disable $SERVICE_NAME"
