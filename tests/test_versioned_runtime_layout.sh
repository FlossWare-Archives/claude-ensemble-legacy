#!/usr/bin/env bash
# Exercise the actual release validator with a real Python venv and the full
# requirements-bearing release layout. A skeletal fixture cannot expose venv
# symlink escapes.
set -Eeuo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/ce-runtime-layout.XXXXXX")"
trap 'rm -rf -- "$WORK"' EXIT

export HOME="$WORK/home"
ROOT="$HOME/.FlossWare/claude-ensemble"
RELEASE="$ROOT/releases/0.1"
mkdir -p "$RELEASE" "$ROOT/shared/claude-code" "$ROOT/shared/config" \
  "$ROOT/shared/data/memory" "$ROOT/shared/data/learning" \
  "$ROOT/shared/runtime" "$ROOT/shared/logs" "$ROOT/shared/activation-backups" \
  "$WORK/bin" "$WORK/units"

# Keep this test fully offline. The real venv is created, but requirements and
# model weights are not installed or downloaded.
tar -C "$REPO_ROOT" \
  --exclude='./.git' --exclude='./.git/*' --exclude='./.venv' \
  --exclude='./claude-global-skills' -cf - . | tar -C "$RELEASE" -xf -
python3 -m venv --copies "$RELEASE/.venv"
[[ -x "$RELEASE/.venv/bin/python" ]] || {
  echo "FAIL: real release virtualenv was not created" >&2
  exit 1
}
[[ -f "$RELEASE/requirements.txt" ]] || {
  echo "FAIL: test release must include requirements.txt" >&2
  exit 1
}

cat > "$WORK/bin/systemctl" <<'MOCK'
#!/usr/bin/env bash
exit 0
MOCK
chmod +x "$WORK/bin/systemctl"
export PATH="$WORK/bin:$PATH"
export ENSEMBLE_INSTALL_ROOT="$ROOT"
export ENSEMBLE_SYSTEMCTL_BIN=true
export ENSEMBLE_USER_UNIT_DIR="$WORK/units"
export ENSEMBLE_CLAUDE_CODE_CONFIG_DIR="$ROOT/shared/claude-code"
export ENSEMBLE_MEMORY_DIR="$ROOT/shared/data/memory"
export ENSEMBLE_LEARNING_DIR="$ROOT/shared/data/learning"
export ENSEMBLE_THOMPSON_STATE_FILE="$ROOT/shared/data/thompson-sampling-state.json"
export ENSEMBLE_GRAPH_STORE="$ROOT/shared/data/graph.json"
export ENSEMBLE_LOG_DIR="$ROOT/shared/logs"
export ENSEMBLE_RUNTIME_DIR="$ROOT/shared/runtime"
export SYSTEMCTL_BIN=true

bash "$RELEASE/tools/ce-deployment" validate 0.1

# Validation itself is the key assertion: it rejects links resolving outside
# the release/shared trees. --copies must therefore keep the real venv valid.
echo "PASS: real --copies virtualenv passes versioned release validation"
