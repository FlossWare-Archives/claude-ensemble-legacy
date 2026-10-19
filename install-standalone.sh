#!/usr/bin/env bash
# First install for a standalone, versioned Claude Ensemble deployment.
#
# Usage:
#   bash install-standalone.sh MAJOR.MINOR [source-checkout]
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
exec bash "$SCRIPT_DIR/tools/install-ce-versioned.sh" "$@"
