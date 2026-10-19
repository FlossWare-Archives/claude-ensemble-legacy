#!/usr/bin/env bash
# Upgrade an installed versioned Claude Ensemble deployment.
#
# Usage:
#   bash upgrade.sh MAJOR.MINOR [source-checkout]
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
exec bash "$SCRIPT_DIR/tools/upgrade-ce-versioned.sh" "$@"
