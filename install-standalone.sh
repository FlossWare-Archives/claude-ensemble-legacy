#!/usr/bin/env bash
# Install a versioned, standalone Claude Ensemble release.
#
# Usage:
#   bash install-standalone.sh [MAJOR.MINOR [source-checkout]]
# With no version, select the newest published MAJOR.MINOR tag. Explicit
# versions remain available for staging, rollback, and controlled testing.
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
if [[ $# -eq 0 ]]; then
  VERSION="$(bash "$SCRIPT_DIR/tools/select-ce-release-version.sh")"
  printf 'Selected latest tagged Claude Ensemble release: %s\n' "$VERSION"
  exec bash "$SCRIPT_DIR/tools/install-ce-versioned.sh" "$VERSION"
fi
exec bash "$SCRIPT_DIR/tools/install-ce-versioned.sh" "$@"
