#!/usr/bin/env bash
# Exercises the copy-based standalone deployment without touching the user's home or services.
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
TMP_ROOT="$(mktemp -d)"
trap 'rm -rf -- "$TMP_ROOT"' EXIT

SOURCE="$TMP_ROOT/source"
DEPLOY="$TMP_ROOT/opt/claude-ensemble"
mkdir -p "$SOURCE/.git" "$SOURCE/tools"
printf '%s\n' '#!/usr/bin/env bash' '[[ -f "$0" ]]' > "$SOURCE/install.sh"
printf '%s\n' 'v1' > "$SOURCE/payload.txt"
printf '%s\n' 'git metadata must not deploy' > "$SOURCE/.git/config"

ENSEMBLE_INSTALL_ROOT="$DEPLOY" bash "$ROOT/tools/install-ce-standalone.sh" "$SOURCE"
[[ -f "$DEPLOY/install.sh" ]]
[[ -f "$DEPLOY/payload.txt" ]]
[[ ! -e "$DEPLOY/.git" ]]
[[ ! -L "$DEPLOY/install.sh" ]]
[[ "$(cat "$DEPLOY/payload.txt")" == 'v1' ]]

# Re-running stages and replaces the independent deployment with the new source.
printf '%s\n' 'v2' > "$SOURCE/payload.txt"
ENSEMBLE_INSTALL_ROOT="$DEPLOY" bash "$ROOT/tools/install-ce-standalone.sh" "$SOURCE"
[[ "$(cat "$DEPLOY/payload.txt")" == 'v2' ]]
[[ ! -e "$DEPLOY/.git" ]]

# A failed install restores the previous deployment rather than leaving a partial tree.
printf '%s\n' '#!/usr/bin/env bash' 'exit 42' > "$SOURCE/install.sh"
if ENSEMBLE_INSTALL_ROOT="$DEPLOY" bash "$ROOT/tools/install-ce-standalone.sh" "$SOURCE"; then
  printf 'standalone installer unexpectedly accepted a failing deployment\n' >&2
  exit 1
else
  status=$?
  [[ "$status" -eq 42 ]] || { printf 'unexpected failure status: %s\n' "$status" >&2; exit 1; }
fi
[[ "$(cat "$DEPLOY/payload.txt")" == 'v2' ]]
[[ -f "$DEPLOY/install.sh" ]]
grep -q 'exit 42' "$SOURCE/install.sh" || exit 1
if grep -q 'exit 42' "$DEPLOY/install.sh"; then
  printf 'failed deployment was not rolled back\n' >&2
  exit 1
fi

printf 'Standalone copy installer tests passed.\n'
