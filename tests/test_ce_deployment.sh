#!/usr/bin/env bash
# Contract tests for release validation, activation, health checks, and rollback.
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
SCRIPT="$SCRIPT_DIR/../tools/ce-deployment"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
export HOME="$TMP/home"
export ENSEMBLE_INSTALL_ROOT="$HOME/.FlossWare/claude-ensemble"
export CE_TEST_STATE="$TMP/state"
export ENSEMBLE_USER_UNIT_DIR="$TMP/isolated-systemd-units"
mkdir -p "$HOME/.config/systemd/user" "$ENSEMBLE_USER_UNIT_DIR" "$ENSEMBLE_INSTALL_ROOT/releases/0.1/tools" "$ENSEMBLE_INSTALL_ROOT/releases/0.2/tools" "$TMP/bin" "$CE_TEST_STATE/active"

cat > "$TMP/bin/systemctl" <<'MOCK_SYSTEMCTL'
#!/usr/bin/env bash
set -Eeuo pipefail
[[ "${1:-}" == --user ]] && shift
command="${1:-}"
[[ $# -eq 0 ]] || shift
touch "$CE_TEST_STATE/systemctl-called"
case "$command" in
  show-environment|daemon-reload|enable) exit 0 ;;
  stop)
    unit="${1:?}"
    rm -f "$CE_TEST_STATE/active/$unit"
    ;;
  start)
    unit="${1:?}"
    if [[ "${FAIL_START_UNIT:-}" == "$unit" && ! -e "$CE_TEST_STATE/failure-consumed" ]]; then
      touch "$CE_TEST_STATE/failure-consumed"
      exit 1
    fi
    touch "$CE_TEST_STATE/active/$unit"
    ;;
  is-active)
    [[ "${1:-}" == --quiet ]] && shift
    [[ -e "$CE_TEST_STATE/active/${1:?}" ]]
    ;;
  *) echo "unexpected systemctl invocation: $command $*" >&2; exit 2 ;;
esac
MOCK_SYSTEMCTL
cat > "$TMP/bin/python3" <<'MOCK_PYTHON'
#!/usr/bin/env bash
# Run JSON/journal operations with Python; only the HTTP readiness probe is mocked.
set -Eeuo pipefail
if [[ "${1:-}" == "-" ]]; then
  input="$(cat)"
  if grep -q '127.0.0.1:8080/api/v1/ready' <<< "$input"; then exit 0; fi
  printf '%s\n' "$input" | /usr/bin/python3 "$@"
else
  exec /usr/bin/python3 "$@"
fi
MOCK_PYTHON
chmod +x "$TMP/bin/systemctl" "$TMP/bin/python3"
export PATH="$TMP/bin:$PATH"

for version in 0.1 0.2; do
  for service_dir in memory-service thompson-service learning-service alert_service session-messaging graph-service server; do
    cp -a "$SCRIPT_DIR/../$service_dir" "$ENSEMBLE_INSTALL_ROOT/releases/$version/"
  done
  cp "$SCRIPT_DIR/../tools/managed_unit_config.py" "$ENSEMBLE_INSTALL_ROOT/releases/$version/tools/managed_unit_config.py"
  cat > "$ENSEMBLE_INSTALL_ROOT/releases/$version/install.sh" <<'MOCK_INSTALL'
#!/usr/bin/env bash
set -Eeuo pipefail
unit_dir="${ENSEMBLE_USER_UNIT_DIR:-$HOME/.config/systemd/user}"
mkdir -p "$unit_dir"
for unit in claude-memory.service claude-thompson.service claude-learning.service claude-alert.service claude-messenger.service claude-graph.service claude-ensemble.service; do
  printf '[Service]\nExecStart=%s/current/fake-service\n' "$ENSEMBLE_INSTALL_ROOT" > "$unit_dir/$unit"
done
MOCK_INSTALL
  chmod +x "$ENSEMBLE_INSTALL_ROOT/releases/$version/install.sh"
done

# Version labels and release structure are validated without touching services.
bash "$SCRIPT" validate 0.1
[[ ! -L "$ENSEMBLE_INSTALL_ROOT/current" ]] || { echo 'FAIL: validate changed current' >&2; exit 1; }
[[ ! -e "$CE_TEST_STATE/systemctl-called" ]] || { echo 'FAIL: validate invoked systemctl' >&2; exit 1; }
if bash "$SCRIPT" validate 0.1.1 >/dev/null 2>&1; then
  echo 'FAIL: patch version unexpectedly accepted' >&2
  exit 1
fi

# Release symlinks may target shared deployment state, never arbitrary host paths.
ln -s /etc/passwd "$ENSEMBLE_INSTALL_ROOT/releases/0.1/tools/outside-host"
if bash "$SCRIPT" validate 0.1 >/dev/null 2>&1; then
  echo 'FAIL: release symlink escaping deployment root was accepted' >&2
  exit 1
fi
rm "$ENSEMBLE_INSTALL_ROOT/releases/0.1/tools/outside-host"

# A structurally valid release with a broken generated unit must fail preflight
# before activation, systemd calls, or current-symlink mutation.
GRAPH_TEMPLATE="$ENSEMBLE_INSTALL_ROOT/releases/0.2/graph-service/claude-graph.service.template"
cp "$GRAPH_TEMPLATE" "$TMP/graph-template.backup"
sed -i '/^ExecStart=/d' "$GRAPH_TEMPLATE"
if bash "$SCRIPT" validate 0.2 >/dev/null 2>&1; then
  echo 'FAIL: preflight accepted a service unit without ExecStart' >&2
  exit 1
fi
mv "$TMP/graph-template.backup" "$GRAPH_TEMPLATE"
[[ ! -L "$ENSEMBLE_INSTALL_ROOT/current" ]] || { echo 'FAIL: failed preflight changed current' >&2; exit 1; }
[[ ! -e "$CE_TEST_STATE/systemctl-called" ]] || { echo 'FAIL: failed preflight invoked systemctl' >&2; exit 1; }

# The helper itself enforces the same path boundary as the installer.
if ENSEMBLE_INSTALL_ROOT="$TMP/outside-root" bash "$SCRIPT" status >/dev/null 2>&1; then
  echo 'FAIL: deployment root outside HOME was accepted' >&2
  exit 1
fi
if ENSEMBLE_INSTALL_ROOT="$HOME/.FlossWare/.." bash "$SCRIPT" status >/dev/null 2>&1; then
  echo 'FAIL: deployment root ending in .. was accepted' >&2
  exit 1
fi
[[ ! -e "$HOME/releases" ]] || { echo 'FAIL: rejected deployment traversal created a releases directory in HOME' >&2; exit 1; }
ESCAPE_PARENT="$TMP/escape-parent"
if ENSEMBLE_INSTALL_ROOT="$HOME/../escape-parent/root" bash "$SCRIPT" status >/dev/null 2>&1; then
  echo 'FAIL: deployment path escaping HOME through parent traversal was accepted' >&2
  exit 1
fi
[[ ! -e "$ESCAPE_PARENT" ]] || { echo 'FAIL: rejected deployment traversal created a directory outside HOME' >&2; exit 1; }
if ENSEMBLE_STAGING_ONLY=1 ENSEMBLE_INSTALL_ROOT="$HOME/.FlossWare/.." bash "$SCRIPT_DIR/../tools/install-ce-versioned.sh" 0.3 /nonexistent >/dev/null 2>&1; then
  echo 'FAIL: versioned installer accepted a deployment root ending in ..' >&2
  exit 1
fi
[[ ! -e "$HOME/releases" ]] || { echo 'FAIL: rejected installer traversal created a releases directory in HOME' >&2; exit 1; }
if ENSEMBLE_STAGING_ONLY=1 ENSEMBLE_INSTALL_ROOT="$HOME/../escape-parent/root" bash "$SCRIPT_DIR/../tools/install-ce-versioned.sh" 0.3 /nonexistent >/dev/null 2>&1; then
  echo 'FAIL: versioned installer accepted a path escaping HOME through parent traversal' >&2
  exit 1
fi
[[ ! -e "$ESCAPE_PARENT" ]] || { echo 'FAIL: rejected installer traversal created a directory outside HOME' >&2; exit 1; }
OUTSIDE_PARENT="$TMP/outside-parent"
mkdir -p "$OUTSIDE_PARENT"
ln -s "$OUTSIDE_PARENT" "$HOME/external-link"
if ENSEMBLE_INSTALL_ROOT="$HOME/external-link/new/root" bash "$SCRIPT" status >/dev/null 2>&1; then
  echo 'FAIL: deployment path through an external parent symlink was accepted' >&2
  exit 1
fi
[[ ! -e "$OUTSIDE_PARENT/new" ]] || { echo 'FAIL: deployment helper created directories through an external parent symlink' >&2; exit 1; }
if ENSEMBLE_STAGING_ONLY=1 ENSEMBLE_INSTALL_ROOT="$HOME/external-link/new/root" bash "$SCRIPT_DIR/../tools/install-ce-versioned.sh" 0.3 /nonexistent >/dev/null 2>&1; then
  echo 'FAIL: versioned installer accepted a path through an external parent symlink' >&2
  exit 1
fi
[[ ! -e "$OUTSIDE_PARENT/new" ]] || { echo 'FAIL: versioned installer created directories through an external parent symlink' >&2; exit 1; }
rm "$HOME/external-link"
ln -s "$ENSEMBLE_INSTALL_ROOT" "$HOME/ce-root-link"
if ENSEMBLE_INSTALL_ROOT="$HOME/ce-root-link" bash "$SCRIPT" status >/dev/null 2>&1; then
  echo 'FAIL: symlinked deployment root was accepted' >&2
  exit 1
fi
rm "$HOME/ce-root-link"

# Deployment-owned releases/shared paths must themselves be real directories.
LINKED_ROOT="$HOME/.FlossWare/linked-state-root"
mkdir -p "$LINKED_ROOT"
ln -s "$TMP/external-releases" "$LINKED_ROOT/releases"
if ENSEMBLE_INSTALL_ROOT="$LINKED_ROOT" bash "$SCRIPT" status >/dev/null 2>&1; then
  echo 'FAIL: symlinked releases directory was accepted' >&2
  exit 1
fi
rm "$LINKED_ROOT/releases"
ln -s "$TMP/external-shared" "$LINKED_ROOT/shared"
if ENSEMBLE_INSTALL_ROOT="$LINKED_ROOT" bash "$SCRIPT" status >/dev/null 2>&1; then
  echo 'FAIL: symlinked shared directory was accepted' >&2
  exit 1
fi
rm "$LINKED_ROOT/shared"
mkdir -p "$LINKED_ROOT/shared"
ln -s "$TMP/external-environment" "$LINKED_ROOT/shared/config"
mkdir -p "$LINKED_ROOT/shared"
if ENSEMBLE_INSTALL_ROOT="$LINKED_ROOT" bash "$SCRIPT" status >/dev/null 2>&1; then
  echo 'FAIL: symlinked shared config directory was accepted' >&2
  exit 1
fi

# First activation should start all seven mocked services.
bash "$SCRIPT" activate 0.1
[[ "$(bash "$SCRIPT" status | grep '^Current:' | awk '{print $2}')" == 0.1 ]]
for unit in claude-memory.service claude-thompson.service claude-learning.service claude-alert.service claude-messenger.service claude-graph.service claude-ensemble.service; do
  [[ -e "$CE_TEST_STATE/active/$unit" ]] || { echo "FAIL: $unit not active" >&2; exit 1; }
  unit_file="$ENSEMBLE_USER_UNIT_DIR/$unit"
  [[ -f "$unit_file" ]] || { echo "FAIL: generated unit missing from isolated directory: $unit_file" >&2; exit 1; }
  grep -Fq "EnvironmentFile=-$ENSEMBLE_INSTALL_ROOT/shared/config/environment" "$unit_file" || { echo "FAIL: $unit does not use deployment-owned environment file" >&2; exit 1; }
  grep -Fq "ENSEMBLE_LOG_DIR=$ENSEMBLE_INSTALL_ROOT/shared/logs" "$unit_file" || { echo "FAIL: $unit does not isolate logs under shared/" >&2; exit 1; }
  grep -Fq "ENSEMBLE_RUNTIME_DIR=$ENSEMBLE_INSTALL_ROOT/shared/runtime" "$unit_file" || { echo "FAIL: $unit does not isolate runtime state under shared/" >&2; exit 1; }
  grep -Fq "ENSEMBLE_CLAUDE_CODE_CONFIG_DIR=$ENSEMBLE_INSTALL_ROOT/shared/claude-code" "$unit_file" || { echo "FAIL: $unit does not isolate Claude Code configuration" >&2; exit 1; }
  if grep -Eq '/\.claude(/|$)|/\.local/share/claude-ensemble' "$unit_file"; then
    echo "FAIL: managed unit retains a legacy mutable-state path: $unit" >&2
    exit 1
  fi
done
grep -Fq "ENSEMBLE_MEMORY_DIR=$ENSEMBLE_INSTALL_ROOT/shared/data/memory" "$ENSEMBLE_USER_UNIT_DIR/claude-memory.service" || { echo "FAIL: memory data path not isolated" >&2; exit 1; }
grep -Fq "ENSEMBLE_LEARNING_DIR=$ENSEMBLE_INSTALL_ROOT/shared/data/learning" "$ENSEMBLE_USER_UNIT_DIR/claude-learning.service" || { echo "FAIL: learning data path not isolated" >&2; exit 1; }
grep -Fq "ENSEMBLE_ALERT_DIR=$ENSEMBLE_INSTALL_ROOT/shared/data/alerts" "$ENSEMBLE_USER_UNIT_DIR/claude-alert.service" || { echo "FAIL: alert data path not isolated" >&2; exit 1; }
grep -Fq "ENSEMBLE_GRAPH_STORE=$ENSEMBLE_INSTALL_ROOT/shared/data/graph.json" "$ENSEMBLE_USER_UNIT_DIR/claude-graph.service" || { echo "FAIL: graph data path not isolated" >&2; exit 1; }
grep -Fq "ENSEMBLE_THOMPSON_STATE_FILE=$ENSEMBLE_INSTALL_ROOT/shared/data/thompson-sampling-state.json" "$ENSEMBLE_USER_UNIT_DIR/claude-thompson.service" || { echo "FAIL: Thompson state path not isolated" >&2; exit 1; }

# Force one startup failure for 0.2. The helper must restore 0.1 and recover.
if FAIL_START_UNIT=claude-ensemble.service bash "$SCRIPT" activate 0.2; then
  echo 'FAIL: activation unexpectedly succeeded despite injected service failure' >&2
  exit 1
fi
[[ "$(bash "$SCRIPT" status | grep '^Current:' | awk '{print $2}')" == 0.1 ]]
for unit in claude-memory.service claude-thompson.service claude-learning.service claude-alert.service claude-messenger.service claude-graph.service claude-ensemble.service; do
  [[ -e "$CE_TEST_STATE/active/$unit" ]] || { echo "FAIL: rollback did not recover $unit" >&2; exit 1; }
done

# Simulate a hard process interruption after current switched to 0.2 but before
# service startup/health verification. Recovery must use the durable journal.
BACKUP="$ENSEMBLE_INSTALL_ROOT/shared/activation-backups/activation.test"
mkdir -p "$BACKUP" "$ENSEMBLE_INSTALL_ROOT/shared/activation-backups"
: > "$BACKUP/active-before.txt"
for unit in claude-memory.service claude-thompson.service claude-learning.service claude-alert.service claude-messenger.service claude-graph.service claude-ensemble.service; do
  cp -a "$ENSEMBLE_USER_UNIT_DIR/$unit" "$BACKUP/$unit"
  printf "%s\n" "$unit" >> "$BACKUP/active-before.txt"
  rm -f "$CE_TEST_STATE/active/$unit"
done
ln -s releases/0.2 "$ENSEMBLE_INSTALL_ROOT/.current.interrupted"
mv -Tf "$ENSEMBLE_INSTALL_ROOT/.current.interrupted" "$ENSEMBLE_INSTALL_ROOT/current"
python3 - "$ENSEMBLE_INSTALL_ROOT/activation.json" "$BACKUP" <<'JOURNAL'
import json, sys
path, backup = sys.argv[1:]
with open(path, "w", encoding="utf-8") as out:
    json.dump({"schema_version": 1, "previous": "0.1", "target": "0.2", "unit_backup": backup, "active_before_file": backup + "/active-before.txt"}, out)
    out.write("\n")
JOURNAL
bash "$SCRIPT" recover
[[ "$(readlink "$ENSEMBLE_INSTALL_ROOT/current")" == releases/0.1 ]]
[[ ! -e "$ENSEMBLE_INSTALL_ROOT/activation.json" ]]
for unit in claude-memory.service claude-thompson.service claude-learning.service claude-alert.service claude-messenger.service claude-graph.service claude-ensemble.service; do
  [[ -e "$CE_TEST_STATE/active/$unit" ]] || { echo "FAIL: recovery did not restart $unit" >&2; exit 1; }
done
[[ ! -e "$BACKUP" ]]
echo "Interrupted activation recovery contract passed."
bash "$SCRIPT" prune
[[ -d "$ENSEMBLE_INSTALL_ROOT/releases/0.1" ]]
[[ ! -e "$ENSEMBLE_INSTALL_ROOT/releases/0.2" ]] || { echo "FAIL: prune retained a failed release instead of only known-good releases" >&2; exit 1; }
echo 'Versioned deployment contract tests passed.'
if compgen -G "$HOME/.config/systemd/user/claude-*.service" >/dev/null; then
  echo 'FAIL: deployment staging wrote service units into the normal user-unit directory' >&2
  exit 1
fi
unset ENSEMBLE_USER_UNIT_DIR

# Exercise the real installer against a temporary legacy deployment and mocked
# systemd. This verifies profile preservation, migration snapshotting, manifest
# creation, and stable-current unit generation without touching host services.
rm -rf "$CE_TEST_STATE"
mkdir -p "$CE_TEST_STATE/active"
INSTALL_HOME="$TMP/install-home"
INSTALL_ROOT="$INSTALL_HOME/.FlossWare/claude-ensemble"
SOURCE_FIXTURE="$TMP/source-fixture"
mkdir -p "$INSTALL_HOME/.FlossWare/claude-ensemble-profile" "$INSTALL_ROOT/claude-code" "$INSTALL_ROOT/shared/claude-code" "$SOURCE_FIXTURE/tools"
for service_dir in memory-service thompson-service learning-service alert_service session-messaging graph-service server; do
  cp -a "$SCRIPT_DIR/../$service_dir" "$SOURCE_FIXTURE/"
done
cp "$SCRIPT_DIR/../tools/managed_unit_config.py" "$SOURCE_FIXTURE/tools/managed_unit_config.py"
printf '{"custom":"legacy-settings"}\n' > "$INSTALL_ROOT/claude-code/settings.json"
printf '{"custom":"keep-me"}\n' > "$INSTALL_ROOT/claude-code/legacy-only.json"
printf '{"custom":"top-level-legacy"}\n' > "$INSTALL_HOME/.FlossWare/claude-ensemble-profile/legacy-only.json"
printf '{"custom":"shared-wins"}\n' > "$INSTALL_ROOT/shared/claude-code/settings.json"
cat > "$INSTALL_ROOT/install.sh" <<'LEGACY_INSTALL'
#!/usr/bin/env bash
exit 0
LEGACY_INSTALL
cat > "$SOURCE_FIXTURE/install.sh" <<'FIXTURE_INSTALL'
#!/usr/bin/env bash
set -Eeuo pipefail
unit_dir="${ENSEMBLE_USER_UNIT_DIR:-$HOME/.config/systemd/user}"
mkdir -p "$unit_dir"
for unit in claude-memory.service claude-thompson.service claude-learning.service claude-alert.service claude-messenger.service claude-graph.service claude-ensemble.service; do
  printf '[Service]\nExecStart=%s/current/fake-service\n' "$ENSEMBLE_INSTALL_ROOT" > "$unit_dir/$unit"
done
FIXTURE_INSTALL
cp "$SCRIPT" "$SOURCE_FIXTURE/tools/ce-deployment"

# Let the real Python interpreter handle JSON manifest operations while
# short-circuiting only the gateway HTTP readiness probe.
cat > "$TMP/bin/python3" <<'MOCK_PYTHON'
#!/usr/bin/env bash
set -Eeuo pipefail
if [[ "${1:-}" == "-" ]]; then
  input="$(cat)"
  if grep -q '127.0.0.1:8080/api/v1/ready' <<< "$input"; then
    exit 0
  fi
  printf '%s\n' "$input" | /usr/bin/python3 "$@"
else
  exec /usr/bin/python3 "$@"
fi
MOCK_PYTHON
chmod +x "$TMP/bin/python3"
export HOME="$INSTALL_HOME"
export ENSEMBLE_INSTALL_ROOT="$INSTALL_ROOT"
bash "$SCRIPT_DIR/../tools/install-ce-versioned.sh" 0.1 "$SOURCE_FIXTURE"
[[ "$(readlink "$INSTALL_ROOT/current")" == releases/0.1 ]]
[[ -d "$INSTALL_ROOT/releases/0.0" ]]
[[ -L "$INSTALL_ROOT/releases/0.1/claude-code" ]]
[[ -f "$INSTALL_ROOT/shared/claude-code/settings.json" ]] || { echo "FAIL: shared settings.json missing" >&2; exit 1; }
if ! grep -q 'shared-wins' "$INSTALL_ROOT/shared/claude-code/settings.json"; then cat "$INSTALL_ROOT/shared/claude-code/settings.json" >&2; echo "FAIL: migration overwrote existing shared settings.json" >&2; exit 1; fi
[[ -f "$INSTALL_ROOT/shared/claude-code/legacy-only.json" ]] || { echo "FAIL: legacy-only.json was not migrated" >&2; exit 1; }
if ! grep -q 'keep-me' "$INSTALL_ROOT/shared/claude-code/legacy-only.json"; then cat "$INSTALL_ROOT/shared/claude-code/legacy-only.json" >&2; echo "FAIL: legacy-only.json content changed" >&2; exit 1; fi
/usr/bin/python3 - "$INSTALL_ROOT/deployment.json" <<'VERIFY_MANIFEST'
import json, sys
with open(sys.argv[1], encoding="utf-8") as source:
    manifest = json.load(source)
assert manifest["release"] == "0.1"
assert manifest["validation"] == "activated"
VERIFY_MANIFEST
echo "Versioned installer migration contract tests passed."

# Standalone activation must use deployment-owned credential configuration and
# must never create the legacy global secrets.env file.
grep -Fq 'ENSEMBLE_INSTALL_ROOT="$ROOT"' "$SCRIPT" || { echo "FAIL: activation does not pass its deployment root to install.sh" >&2; exit 1; }
python3 - "$SCRIPT_DIR/../install.sh" <<'CREDENTIAL_CONTRACT'
import sys
from pathlib import Path
source = Path(sys.argv[1]).read_text(encoding="utf-8")
start = source.index("# Step 10: Setup credentials file")
end = source.index("# Step 11: Add tools to PATH", start)
block = source[start:end]
standalone = block.index('if [[ "${ENSEMBLE_STANDALONE_INSTALL:-0}" == "1" ]]')
global_file = block.index('SECRETS_FILE="$HOME/.FlossWare/secrets.env"')
assert standalone < global_file, "standalone guard must precede legacy global credentials path"
standalone_block = block[standalone:block.index("\nelse\n", standalone)]
assert "ENSEMBLE_INSTALL_ROOT" in standalone_block
assert "shared/config/environment" in standalone_block
assert "not creating ~/.FlossWare/secrets.env" in standalone_block
assert "cat >" not in standalone_block, "standalone mode must not create credential files"
CREDENTIAL_CONTRACT
echo "Standalone credential isolation contract passed."

# Exercise the real seven service installers through a logical current symlink.
# They must render stable-current ExecStart paths rather than pinned release paths.
SERVICE_HOME="$TMP/service-home"
SERVICE_ROOT="$SERVICE_HOME/.FlossWare/claude-ensemble"
SERVICE_RELEASE="$SERVICE_ROOT/releases/0.1"
SERVICE_UNIT_DIR="$SERVICE_HOME/staged-systemd-units"
mkdir -p "$SERVICE_RELEASE" "$SERVICE_HOME/.config/systemd/user" "$SERVICE_UNIT_DIR"
export ENSEMBLE_USER_UNIT_DIR="$SERVICE_UNIT_DIR"
for service_dir in memory-service thompson-service learning-service alert_service session-messaging graph-service server; do
  cp -a "$SCRIPT_DIR/../$service_dir" "$SERVICE_RELEASE/"
done
ln -s releases/0.1 "$SERVICE_ROOT/current"
export HOME="$SERVICE_HOME"
export ENSEMBLE_INSTALL_ROOT="$SERVICE_ROOT"
for installer in memory-service/install.sh thompson-service/install.sh learning-service/install.sh alert_service/install.sh session-messaging/install.sh graph-service/install.sh server/install.sh; do
  ENSEMBLE_DEFER_SERVICE_START=1 bash "$SERVICE_ROOT/current/$installer"
done
for unit in claude-memory.service claude-thompson.service claude-learning.service claude-alert.service claude-messenger.service claude-graph.service claude-ensemble.service; do
  unit_file="$SERVICE_UNIT_DIR/$unit"
  [[ -f "$unit_file" ]] || { echo "FAIL: real installer did not generate $unit" >&2; exit 1; }
  grep -Fq "$SERVICE_ROOT/current" "$unit_file" || { echo "FAIL: $unit does not use stable current path" >&2; cat "$unit_file" >&2; exit 1; }
  if grep -Eq "releases/0\.1" "$unit_file"; then echo "FAIL: $unit pins release 0.1" >&2; exit 1; fi
done
if compgen -G "$SERVICE_HOME/.config/systemd/user/claude-*.service" >/dev/null; then
  echo 'FAIL: real service installers ignored ENSEMBLE_USER_UNIT_DIR' >&2
  exit 1
fi
unset ENSEMBLE_USER_UNIT_DIR
echo "Stable-current service installer path contract passed."

# Safe versioned uninstall contract: default uninstall stops/removes only CE units
# and the active pointer while preserving releases and shared user data.
UNINSTALL_HOME="$TMP/uninstall-home"
UNINSTALL_ROOT="$UNINSTALL_HOME/.FlossWare/claude-ensemble"
UNINSTALL_UNITS="$UNINSTALL_HOME/.config/systemd/user"
mkdir -p "$UNINSTALL_ROOT/releases/0.1" "$UNINSTALL_ROOT/shared/data/memory" "$UNINSTALL_UNITS" "$TMP/uninstall-bin"
ln -s releases/0.1 "$UNINSTALL_ROOT/current"
printf '{"schema_version":1,"active":"0.1","previous":null}\n' > "$UNINSTALL_ROOT/known-good.json"
printf 'preserve-me\n' > "$UNINSTALL_ROOT/shared/data/memory/record.jsonl"
for unit in claude-memory.service claude-thompson.service claude-learning.service claude-alert.service claude-messenger.service claude-graph.service claude-ensemble.service; do
  printf '[Service]\nExecStart=/test/current/service\n' > "$UNINSTALL_UNITS/$unit"
done
cat > "$TMP/uninstall-bin/systemctl-mock" <<'MOCK_SYSTEMCTL'
#!/usr/bin/env bash
set -Eeuo pipefail
case "${2:-}" in
  stop|daemon-reload) exit 0 ;;
  is-active) exit 1 ;;
  *) exit 0 ;;
esac
MOCK_SYSTEMCTL
chmod +x "$TMP/uninstall-bin/systemctl-mock"
HOME="$UNINSTALL_HOME" ENSEMBLE_INSTALL_ROOT="$UNINSTALL_ROOT" ENSEMBLE_USER_UNIT_DIR="$UNINSTALL_UNITS" ENSEMBLE_SYSTEMCTL_BIN="$TMP/uninstall-bin/systemctl-mock" bash "$SCRIPT_DIR/../tools/ce-deployment" uninstall
[[ ! -e "$UNINSTALL_ROOT/current" && ! -L "$UNINSTALL_ROOT/current" ]] || { echo "FAIL: uninstall retained current symlink" >&2; exit 1; }
[[ ! -e "$UNINSTALL_ROOT/known-good.json" ]] || { echo "FAIL: uninstall retained known-good activation state" >&2; exit 1; }
[[ -f "$UNINSTALL_ROOT/shared/data/memory/record.jsonl" ]] || { echo "FAIL: uninstall deleted shared data without --purge" >&2; exit 1; }
[[ -d "$UNINSTALL_ROOT/releases/0.1" ]] || { echo "FAIL: uninstall deleted immutable releases without --purge" >&2; exit 1; }
if compgen -G "$UNINSTALL_UNITS/claude-*.service" >/dev/null; then echo "FAIL: uninstall left managed CE units" >&2; exit 1; fi
# The helper must remain callable from the immutable release after the default
# uninstall removes current. Explicit purge then deletes releases and shared data.
HOME="$UNINSTALL_HOME" ENSEMBLE_INSTALL_ROOT="$UNINSTALL_ROOT" ENSEMBLE_USER_UNIT_DIR="$UNINSTALL_UNITS" ENSEMBLE_SYSTEMCTL_BIN="$TMP/uninstall-bin/systemctl-mock" bash "$SCRIPT_DIR/../tools/ce-deployment" uninstall --purge
[[ ! -e "$UNINSTALL_ROOT" && ! -L "$UNINSTALL_ROOT" ]] || { echo "FAIL: --purge retained deployment root" >&2; exit 1; }
echo "Versioned uninstall preservation/purge contract passed."
