#!/usr/bin/env bash
# Stage and activate a newer MAJOR.MINOR release, retaining the previous release for rollback.
set -Eeuo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT="${ENSEMBLE_INSTALL_ROOT:-$HOME/.FlossWare/claude-ensemble}"
case "$ROOT" in "~") ROOT="$HOME";; "~/"*) ROOT="$HOME/${ROOT#\~/}";; esac
if [[ $# -lt 1 || $# -gt 2 || ! "$1" =~ ^[0-9]+\.[0-9]+$ ]]; then
  printf 'Usage: %s MAJOR.MINOR [source-checkout]\n' "$0" >&2
  exit 2
fi
[[ -L "$ROOT/current" ]] || { printf 'ERROR: no active CE release at %s/current; use the installer first.\n' "$ROOT" >&2; exit 1; }
target="$(readlink -- "$ROOT/current")"
[[ "$target" =~ ^releases/([0-9]+\.[0-9]+)$ ]] || { printf 'ERROR: current release link is invalid: %s\n' "$target" >&2; exit 1; }
active="${BASH_REMATCH[1]}"
python3 - "$active" "$1" <<'PY'
import sys
def version(value):
    major, minor = value.split(".")
    return int(major), int(minor)
if version(sys.argv[2]) <= version(sys.argv[1]):
    raise SystemExit(f"ERROR: upgrade version {sys.argv[2]} must be newer than active release {sys.argv[1]}")
PY
if [[ $# -eq 2 ]]; then
  exec bash "$ROOT_DIR/install-ce-versioned.sh" "$1" "$2"
else
  exec bash "$ROOT_DIR/install-ce-versioned.sh" "$1"
fi
