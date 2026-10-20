#!/usr/bin/env bash
set -Eeuo pipefail
REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
BACKUP_SCRIPT="$REPO_ROOT/tools/backup-ce-state.sh"
TMP_HOME="$(mktemp -d)"
trap 'rm -rf -- "$TMP_HOME"' EXIT
export HOME="$TMP_HOME"
export ENSEMBLE_INSTALL_ROOT="$HOME/.FlossWare/claude-ensemble"
mkdir -p "$ENSEMBLE_INSTALL_ROOT"

output="$(bash "$BACKUP_SCRIPT" "0.2" "$ENSEMBLE_INSTALL_ROOT")"
backup_path="${output%%
[[ -d "$backup_path" ]]
name="$(basename -- "$backup_path")"
[[ "$name" =~ ^0\.2_[0-9]{4}-[0-9]{2}-[0-9]{2}_[0-9]{2}-[0-9]{2}-[0-9]{2}-[0-9]{3}(_[0-9]{2})?$ ]] || {
  printf 'Unexpected versioned backup name: %s\n' "$name" >&2
  exit 1
}
[[ -f "$backup_path/manifest.txt" ]]
grep -Fxq "reason=0.2" "$backup_path/manifest.txt"

output="$(bash "$BACKUP_SCRIPT" "pre-install-0.2" "$ENSEMBLE_INSTALL_ROOT")"
backup_path="${output#Pre-operation backup created: }"
name="$(basename -- "$backup_path")"
[[ "$name" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}_[0-9]{2}-[0-9]{2}-[0-9]{2}-[0-9]{3}-pre-install-0\.2-[0-9]+$ ]] || {
  printf 'Unexpected reason-oriented backup name: %s\n' "$name" >&2
  exit 1
}
printf 'PASS: version-first millisecond backup names and legacy reason naming\n'
\n'*}"
backup_path="${backup_path#Pre-operation backup created: }"
[[ -d "$backup_path" ]]
name="$(basename -- "$backup_path")"
[[ "$name" =~ ^0\.2_[0-9]{4}-[0-9]{2}-[0-9]{2}_[0-9]{2}-[0-9]{2}-[0-9]{2}-[0-9]{3}(_[0-9]{2})?$ ]] || {
  printf 'Unexpected versioned backup name: %s\n' "$name" >&2
  exit 1
}
[[ -f "$backup_path/manifest.txt" ]]
grep -Fxq "reason=0.2" "$backup_path/manifest.txt"

output="$(bash "$BACKUP_SCRIPT" "pre-install-0.2" "$ENSEMBLE_INSTALL_ROOT")"
backup_path="${output%%
name="$(basename -- "$backup_path")"
[[ "$name" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}_[0-9]{2}-[0-9]{2}-[0-9]{2}-[0-9]{3}-pre-install-0\.2-[0-9]+$ ]] || {
  printf 'Unexpected reason-oriented backup name: %s\n' "$name" >&2
  exit 1
}
printf 'PASS: version-first millisecond backup names and legacy reason naming\n'
\n'*}"
backup_path="${backup_path#Pre-operation backup created: }"
name="$(basename -- "$backup_path")"
[[ "$name" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}_[0-9]{2}-[0-9]{2}-[0-9]{2}-[0-9]{3}-pre-install-0\.2-[0-9]+$ ]] || {
  printf 'Unexpected reason-oriented backup name: %s\n' "$name" >&2
  exit 1
}
printf 'PASS: version-first millisecond backup names and legacy reason naming\n'
