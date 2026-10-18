#!/usr/bin/env bash
# Install the canonical Claude Ensemble REST gateway as a systemd user service.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_NAME="claude-ensemble.service"
SERVICE_TEMPLATE="$REPO_ROOT/server/claude-ensemble.service.template"
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
  echo "REST gateway installed and enabled; start deferred to the top-level installer."
  exit 0
fi
"$SYSTEMCTL_BIN" --user start "$SERVICE_NAME"

if ! "$SYSTEMCTL_BIN" --user is-active --quiet "$SERVICE_NAME"; then
  echo "REST gateway failed to start. Check: journalctl --user -u $SERVICE_NAME" >&2
  exit 1
fi

# A running unit only establishes process liveness. Do not report a successful
# install until the gateway REST endpoint and required Graph/Memory dependencies
# answer their health probes. Keep this bounded so an unavailable dependency
# produces a useful failure instead of hanging installation indefinitely.
if ! python3 - <<'PY'
import time
import urllib.error
import urllib.request

url = __import__("os").environ.get("ENSEMBLE_READINESS_URL", "http://127.0.0.1:8080/api/v1/ready")
timeout = float(__import__("os").environ.get("ENSEMBLE_READINESS_TIMEOUT_SECONDS", "20"))
deadline = time.monotonic() + timeout
last_error = "readiness endpoint did not become ready"
while time.monotonic() < deadline:
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            if response.status == 200:
                print("REST gateway and required dependencies are ready.")
                raise SystemExit(0)
            last_error = f"readiness endpoint returned HTTP {response.status}"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        last_error = str(exc)
    time.sleep(0.5)
print(f"ERROR: REST gateway is active but not ready: {last_error}", file=__import__("sys").stderr)
raise SystemExit(1)
PY
then
  echo "REST gateway is active but dependencies are not ready. Check Graph, Memory, and: journalctl --user -u $SERVICE_NAME" >&2
  exit 1
fi

echo "REST gateway installed and ready: $SERVICE_NAME"
