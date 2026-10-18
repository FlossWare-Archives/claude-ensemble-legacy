#!/usr/bin/env bash
# Build a self-contained CE deployment outside the developer's working checkout.
#
# Usage:
#   bash tools/install-ce-standalone.sh [source-checkout]
#   curl -fsSL https://raw.githubusercontent.com/FlossWare/claude-ensemble/main/tools/install-ce-standalone.sh | bash
#
# The deployed tree contains no .git directory. Claude profiles and service
# installers receive independent copies of managed assets, not links into a
# developer checkout.

set -Eeuo pipefail

DEPLOY_ROOT="${ENSEMBLE_INSTALL_ROOT:-$HOME/.local/opt/claude-ensemble}"
case "$DEPLOY_ROOT" in
  "~") DEPLOY_ROOT="$HOME" ;;
  "~/"*) DEPLOY_ROOT="$HOME/${DEPLOY_ROOT#\~/}" ;;
esac

# Canonicalize the parent, but do not follow a symlink at the deployment path.
TMP_PARENT="$(dirname -- "$DEPLOY_ROOT")"
mkdir -p -- "$TMP_PARENT"
TMP_PARENT="$(cd -- "$TMP_PARENT" && pwd -P)"
DEPLOY_ROOT="$TMP_PARENT/$(basename -- "$DEPLOY_ROOT")"
HOME_REAL="$(cd -- "$HOME" && pwd -P)"
case "$DEPLOY_ROOT" in
  "$HOME_REAL"/*) ;;
  *)
    printf 'ERROR: Deployment path must be a child of HOME, not HOME itself: %s\n' "$DEPLOY_ROOT" >&2
    exit 1
    ;;
esac

if [[ -L "$DEPLOY_ROOT" ]]; then
  printf 'ERROR: Refusing to replace symlinked deployment path: %s\n' "$DEPLOY_ROOT" >&2
  exit 1
fi
if [[ -e "$DEPLOY_ROOT" && ! -d "$DEPLOY_ROOT" ]]; then
  printf 'ERROR: Deployment path exists and is not a directory: %s\n' "$DEPLOY_ROOT" >&2
  exit 1
fi
if [[ -d "$DEPLOY_ROOT/.git" || -f "$DEPLOY_ROOT/.git" ]]; then
  printf 'ERROR: Deployment path contains Git metadata; refusing to replace a working checkout: %s\n' "$DEPLOY_ROOT" >&2
  printf 'Choose a separate ENSEMBLE_INSTALL_ROOT or move the checkout first.\n' >&2
  exit 1
fi
WORK_ROOT="$(mktemp -d "$TMP_PARENT/.claude-ensemble-install.XXXXXX")"
STAGING="$WORK_ROOT/deployment"
SOURCE_DIR=""

cleanup() {
  rm -rf -- "$WORK_ROOT"
}
trap cleanup EXIT

if [[ "$#" -gt 1 ]]; then
  printf 'Usage: %s [source-checkout]\n' "$0" >&2
  exit 2
fi

if [[ "$#" -eq 1 ]]; then
  SOURCE_DIR="$1"
elif [[ -f "${BASH_SOURCE[0]}" ]]; then
  CANDIDATE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd -P)"
  if [[ -f "$CANDIDATE/install.sh" ]]; then
    SOURCE_DIR="$CANDIDATE"
  fi
fi

if [[ -n "$SOURCE_DIR" ]]; then
  if [[ ! -f "$SOURCE_DIR/install.sh" ]]; then
    printf 'ERROR: Source checkout does not contain install.sh: %s\n' "$SOURCE_DIR" >&2
    exit 1
  fi
  SOURCE_DIR="$(cd -- "$SOURCE_DIR" && pwd -P)"
  printf 'Preparing independent deployment from: %s\n' "$SOURCE_DIR"
  mkdir -p -- "$STAGING"
  tar -C "$SOURCE_DIR" --exclude='./.git' --exclude='./.git/*' --exclude='*/.git' \
      --exclude='__pycache__' --exclude='*.pyc' -cf - . | tar -C "$STAGING" -xf -
else
  printf 'Downloading Claude Ensemble source archive...\n'
  mkdir -p -- "$STAGING"
  curl -fsSL https://github.com/FlossWare/claude-ensemble/archive/refs/heads/main.tar.gz \
    | tar -xz -C "$STAGING" --strip-components=1
fi

if [[ ! -f "$STAGING/install.sh" || -d "$STAGING/.git" ]]; then
  printf 'ERROR: Staged deployment is incomplete or unexpectedly contains .git.\n' >&2
  exit 1
fi

BACKUP_ROOT="${DEPLOY_ROOT}.previous.$$"
if [[ -e "$BACKUP_ROOT" || -L "$BACKUP_ROOT" ]]; then
  printf 'ERROR: Backup path already exists: %s\n' "$BACKUP_ROOT" >&2
  exit 1
fi

if [[ -e "$DEPLOY_ROOT" ]]; then
  mv -- "$DEPLOY_ROOT" "$BACKUP_ROOT"
fi
if ! mv -- "$STAGING" "$DEPLOY_ROOT"; then
  if [[ -e "$BACKUP_ROOT" ]]; then mv -- "$BACKUP_ROOT" "$DEPLOY_ROOT"; fi
  printf 'ERROR: Could not activate staged deployment.\n' >&2
  exit 1
fi

printf '\nInstalling Claude Ensemble from independent deployment: %s\n\n' "$DEPLOY_ROOT"
if bash "$DEPLOY_ROOT/install.sh" "$DEPLOY_ROOT"; then
  if [[ -e "$BACKUP_ROOT" ]]; then rm -rf -- "$BACKUP_ROOT"; fi
  printf '\nIndependent CE deployment installed at %s\n' "$DEPLOY_ROOT"
  printf 'This deployment is separate from your working Git checkout.\n'
else
  status=$?
  printf '\nERROR: CE installation failed (exit %s). Restoring the prior deployment when available.\n' "$status" >&2
  rm -rf -- "$DEPLOY_ROOT"
  if [[ -e "$BACKUP_ROOT" ]]; then mv -- "$BACKUP_ROOT" "$DEPLOY_ROOT"; fi
  exit "$status"
fi
