#!/usr/bin/env bash
# Uninstall a versioned standalone Claude Ensemble deployment.
#
# Usage:
#   bash uninstall.sh
#   bash uninstall.sh --purge
#
# The default preserves immutable releases and shared data. --purge permanently
# deletes the deployment root and its shared data after CE services are stopped.
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT="${ENSEMBLE_INSTALL_ROOT:-$HOME/.FlossWare/claude-ensemble}"
case "$ROOT" in "~") ROOT="$HOME";; "~/"*) ROOT="$HOME/${ROOT#\~/}";; esac
ROOT="${ROOT%/}"

[[ ! -L "$ROOT" ]] || {
  printf 'ERROR: Deployment root must not be a symlink: %s\n' "$ROOT" >&2
  exit 1
}
[[ -d "$ROOT" ]] || {
  printf 'ERROR: Claude Ensemble deployment root does not exist: %s\n' "$ROOT" >&2
  exit 1
}

exec bash "$SCRIPT_DIR/tools/ce-deployment" uninstall "$@"
