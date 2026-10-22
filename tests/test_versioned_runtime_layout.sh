#!/usr/bin/env bash
# Validate real release virtualenvs and exercise offline activation/rollback.
# Disposable HOME/release trees prevent tests from touching a developer install.
set -Eeuo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/ce-runtime-layout.XXXXXX")"
READY_PID=""
cleanup() {
  if [[ -n "$READY_PID" ]]; then kill "$READY_PID" 2>/dev/null || true; fi
  rm -rf -- "$WORK"
}
trap cleanup EXIT

export HOME="$WORK/home"
ROOT="$HOME/.FlossWare/claude-ensemble"
mkdir -p "$ROOT/releases" "$WORK/bin" "$WORK/units" "$WORK/empty-pip-cache"
ACTIVE_FILE="$WORK/active-units.txt"
SYSTEMCTL_LOG="$WORK/systemctl.log"
: > "$ACTIVE_FILE"
: > "$SYSTEMCTL_LOG"

cat > "$WORK/bin/systemctl" <<'MOCK'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "$SYSTEMCTL_LOG"
if [[ "${1:-}" == "--user" ]]; then shift; fi
command="${1:-}"
if [[ $# -gt 0 ]]; then shift; fi
case "$command" in
  show-environment|daemon-reload) exit 0 ;;
  stop)
    unit="${1:?missing unit}"
    grep -Fvx "$unit" "$ACTIVE_FILE" > "$ACTIVE_FILE.tmp" || true
    mv "$ACTIVE_FILE.tmp" "$ACTIVE_FILE"
    exit 0
    ;;
  start)
    unit="${1:?missing unit}"
    grep -Fxq "$unit" "$ACTIVE_FILE" || printf '%s\n' "$unit" >> "$ACTIVE_FILE"
    exit 0
    ;;
  is-active)
    [[ "${1:-}" == "--quiet" ]] && shift
    grep -Fxq "${1:?missing unit}" "$ACTIVE_FILE"
    ;;
  *) exit 0 ;;
esac
MOCK
chmod +x "$WORK/bin/systemctl"
export PATH="$WORK/bin:$PATH"
export SYSTEMCTL_BIN="$WORK/bin/systemctl"
export ENSEMBLE_SYSTEMCTL_BIN="$WORK/bin/systemctl"
export ENSEMBLE_USER_UNIT_DIR="$WORK/units"
export ENSEMBLE_INSTALL_ROOT="$ROOT"
export ENSEMBLE_CLAUDE_CODE_CONFIG_DIR="$ROOT/shared/claude-code"
export ENSEMBLE_MEMORY_DIR="$ROOT/shared/data/memory"
export ENSEMBLE_LEARNING_DIR="$ROOT/shared/data/learning"
export ENSEMBLE_THOMPSON_STATE_FILE="$ROOT/shared/data/thompson-sampling-state.json"
export ENSEMBLE_GRAPH_STORE="$ROOT/shared/data/graph.json"
export ENSEMBLE_LOG_DIR="$ROOT/shared/logs"
export ENSEMBLE_RUNTIME_DIR="$ROOT/shared/runtime"

# Fail closed if activation or rollback attempts package/model acquisition.
export ENSEMBLE_SKIP_PYTHON_PROVISION=1
export ENSEMBLE_SKIP_MODEL_PROVISION=1
export PIP_NO_INDEX=1
export PIP_CACHE_DIR="$WORK/empty-pip-cache"
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export HTTP_PROXY="http://127.0.0.1:1"
export HTTPS_PROXY="http://127.0.0.1:1"
export ENSEMBLE_GATEWAY_READINESS_TIMEOUT_SECONDS=3

python3 - "$WORK/ready-port" <<'PY' >/dev/null 2>&1 &
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import sys

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"ok":true}')
    def log_message(self, *args):
        pass

server = HTTPServer(("127.0.0.1", 0), Handler)
Path(sys.argv[1]).write_text(str(server.server_port), encoding="utf-8")
server.serve_forever()
PY
READY_PID=$!
for _ in $(seq 1 100); do
  [[ -s "$WORK/ready-port" ]] && break
  sleep 0.02
done
[[ -s "$WORK/ready-port" ]] || { echo "FAIL: readiness stub did not start" >&2; exit 1; }
export ENSEMBLE_READY_URL="http://127.0.0.1:$(cat "$WORK/ready-port")/ready"

create_release() {
  local version="$1" release="$ROOT/releases/$1"
  mkdir -p "$release" "$ROOT/shared/claude-code" "$ROOT/shared/config" \
    "$ROOT/shared/data/memory" "$ROOT/shared/data/learning" \
    "$ROOT/shared/runtime" "$ROOT/shared/logs" "$ROOT/shared/activation-backups"
  tar -C "$REPO_ROOT" \
    --exclude='./.git' --exclude='./.git/*' --exclude='./.venv' \
    --exclude='./claude-global-skills' --exclude='./__pycache__' \
    -cf - . | tar -C "$release" -xf -
  python3 -m venv --copies "$release/.venv"
  [[ -x "$release/.venv/bin/python" && -f "$release/requirements.txt" ]] || {
    echo "FAIL: release $version lacks a real runtime or requirements.txt" >&2
    exit 1
  }
  bash "$release/tools/ce-deployment" validate "$version"
}

create_release 0.1
create_release 0.2

echo "=== Activate first release without network access ==="
bash "$ROOT/releases/0.1/tools/ce-deployment" activate 0.1 > "$WORK/activate-01.log" 2>&1 || {
  cat "$WORK/activate-01.log" >&2
  exit 1
}
grep -q "Using the pre-provisioned standalone release runtime" "$WORK/activate-01.log"
[[ "$(readlink "$ROOT/current")" == "releases/0.1" ]]

echo "=== Activate second release without network access ==="
bash "$ROOT/current/tools/ce-deployment" activate 0.2 > "$WORK/activate-02.log" 2>&1 || {
  cat "$WORK/activate-02.log" >&2
  exit 1
}
grep -q "Using the pre-provisioned standalone release runtime" "$WORK/activate-02.log"
[[ "$(readlink "$ROOT/current")" == "releases/0.2" ]]

echo "=== Roll back to the prior release without network access ==="
bash "$ROOT/current/tools/ce-deployment" rollback > "$WORK/rollback.log" 2>&1 || {
  cat "$WORK/rollback.log" >&2
  exit 1
}
grep -q "Using the pre-provisioned standalone release runtime" "$WORK/rollback.log"
[[ "$(readlink "$ROOT/current")" == "releases/0.1" ]]
[[ ! -e "$ROOT/activation.json" ]]

if grep -Eiq 'pip install|Provisioning the configured local embedding model|Downloading model' \
    "$WORK/activate-01.log" "$WORK/activate-02.log" "$WORK/rollback.log"; then
  echo "FAIL: activation or rollback attempted dependency/model provisioning" >&2
  exit 1
fi

echo "PASS: real venv layout, activation, and rollback work offline without runtime mutation"
