#!/usr/bin/env bash
# Upgrade an active versioned Claude Ensemble deployment.
#
# Usage:
#   tools/upgrade-ce-versioned.sh MAJOR.MINOR [source-checkout]
#
# Releases are immutable. An upgrade stages a new release, validates its units,
# and activates it through the deployment manager, which rolls back on failure.
set -Eeuo pipefail

if [[ "$#" -lt 1 || "$#" -gt 2 || ! "$1" =~ ^[0-9]+\.[0-9]+$ ]]; then
  printf 'Usage: %s MAJOR.MINOR [source-checkout]\n' "$0" >&2
  exit 2
fi

VERSION="$1"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
SOURCE_DIR="${2:-$(cd -- "$SCRIPT_DIR/.." && pwd -P)}"
ROOT="${ENSEMBLE_INSTALL_ROOT:-$HOME/.FlossWare/claude-ensemble}"
case "$ROOT" in "~") ROOT="$HOME";; "~/"*) ROOT="$HOME/${ROOT#\~/}";; esac
ROOT="${ROOT%/}"

command -v python3 >/dev/null 2>&1 || {
  printf 'ERROR: python3 is required to validate the deployment path.\n' >&2
  exit 1
}
[[ ! -L "$ROOT" ]] || {
  printf 'ERROR: Deployment root must not be a symlink: %s\n' "$ROOT" >&2
  exit 1
}
HOME_REAL="$(cd -- "$HOME" && pwd -P)"
ROOT="$(python3 -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' "$ROOT")"
case "$ROOT" in "$HOME_REAL"/*) ;; *) printf 'ERROR: Deployment root must be beneath HOME: %s\n' "$ROOT" >&2; exit 1;; esac

CURRENT="$ROOT/current"
[[ -L "$CURRENT" ]] || {
  printf 'ERROR: No active versioned deployment at %s; use install-ce-versioned.sh for a first install.\n' "$ROOT" >&2
  exit 1
}
target="$(readlink -- "$CURRENT")"
[[ "$target" =~ ^releases/([0-9]+\.[0-9]+)$ ]] || {
  printf 'ERROR: Current release pointer is invalid: %s\n' "$target" >&2
  exit 1
}
ACTIVE_VERSION="${BASH_REMATCH[1]}"
[[ -d "$ROOT/$target" && ! -L "$ROOT/$target" ]] || {
  printf 'ERROR: Current release target is missing or unsafe: %s\n' "$ROOT/$target" >&2
  exit 1
}
[[ "$VERSION" != "$ACTIVE_VERSION" ]] || {
  printf 'ERROR: Release %s is already active; choose a new MAJOR.MINOR version.\n' "$VERSION" >&2
  exit 1
}
[[ -f "$SOURCE_DIR/install.sh" ]] || {
  printf 'ERROR: Source checkout lacks install.sh: %s\n' "$SOURCE_DIR" >&2
  exit 1
}

printf 'Upgrading Claude Ensemble from %s to %s.\n' "$ACTIVE_VERSION" "$VERSION"
exec bash "$SCRIPT_DIR/install-ce-versioned.sh" "$VERSION" "$SOURCE_DIR"
