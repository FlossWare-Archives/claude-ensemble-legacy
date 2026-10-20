#!/usr/bin/env bash
set -Eeuo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
TMP="$(mktemp -d)"
trap 'rm -rf -- "$TMP"' EXIT
HOME="$TMP/home"
export HOME
mkdir -p "$HOME/source/tools"
cp "$REPO_ROOT/tools/backup-ce-state.sh" "$HOME/source/tools/"
cp "$REPO_ROOT/tools/resolve-ce-version.sh" "$HOME/source/tools/"
git -C "$HOME/source" init -q
git -C "$HOME/source" -c user.name=Test -c user.email=test@example.invalid commit --allow-empty -qm init
git -C "$HOME/source" tag 0.1

ROOT="$HOME/.FlossWare/claude-ensemble"
output="$(bash "$HOME/source/tools/backup-ce-state.sh" pre-install "$ROOT")"
backup_path="$(printf '%s\\n' "$output" | sed -n '1s/^Pre-operation backup created: //p')"
[[ -d "$backup_path" ]]
backup_name="$(basename -- "$backup_path")"
[[ "$backup_name" =~ ^0\.2_[0-9]{4}-[0-9]{2}-[0-9]{2}_[0-9]{2}-[0-9]{2}-[0-9]{2}-[0-9]{3}(_[0-9]{2})?$ ]] || {
  printf 'Unexpected automatically versioned backup name: %s\n' "$backup_name" >&2
  exit 1
}
grep -Fxq 'version=0.2' "$backup_path/manifest.txt"
grep -Fxq 'reason=pre-install' "$backup_path/manifest.txt"

# Repeated snapshots preserve the same version while obtaining distinct names.
output2="$(bash "$HOME/source/tools/backup-ce-state.sh" pre-install "$ROOT")"
backup_path2="$(printf '%s\\n' "$output2" | sed -n '1s/^Pre-operation backup created: //p')"
[[ -d "$backup_path2" && "$backup_path2" != "$backup_path" ]]
[[ "$(basename -- "$backup_path2")" =~ ^0\.2_[0-9]{4}-[0-9]{2}-[0-9]{2}_[0-9]{2}-[0-9]{2}-[0-9]{2}-[0-9]{3}(_[0-9]{2})?$ ]]

printf 'PASS: automatic versioned backup names, manifest version, and repeated snapshots\n'
