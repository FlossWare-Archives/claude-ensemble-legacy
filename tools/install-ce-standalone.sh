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

DEPLOY_ROOT="${ENSEMBLE_INSTALL_ROOT:-$HOME/.FlossWare/claude-ensemble}"
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

for required in curl tar find readlink systemctl git; do
  command -v "$required" >/dev/null 2>&1 || { printf 'ERROR: Required command not found: %s\\n' "$required" >&2; exit 1; }
done
systemctl --user show-environment >/dev/null 2>&1 || { printf 'ERROR: systemd user manager is unavailable. Run from your logged-in user session.\\n' >&2; exit 1; }
SERVICE_UNITS=(claude-memory.service claude-thompson.service claude-learning.service claude-alert.service claude-messenger.service claude-graph.service claude-ensemble.service)
STOP_ORDER=(claude-ensemble.service claude-graph.service claude-messenger.service claude-alert.service claude-learning.service claude-thompson.service claude-memory.service)
START_ORDER=(claude-memory.service claude-thompson.service claude-learning.service claude-alert.service claude-messenger.service claude-graph.service claude-ensemble.service)
ACTIVE_BEFORE=()
for unit in "${SERVICE_UNITS[@]}"; do
  systemctl --user is-active --quiet "$unit" && ACTIVE_BEFORE+=("$unit") || true
done
stop_ce_services() {
  local unit
  for unit in "${STOP_ORDER[@]}"; do systemctl --user stop "$unit" >/dev/null 2>&1 || true; done
}
restore_preinstall_services() {
  local unit
  systemctl --user daemon-reload >/dev/null 2>&1 || true
  for unit in "${ACTIVE_BEFORE[@]}"; do systemctl --user start "$unit" >/dev/null 2>&1 || true; done
}
start_and_verify_services() {
  local unit
  systemctl --user daemon-reload
  for unit in "${START_ORDER[@]}"; do systemctl --user start "$unit"; done
  for unit in "${SERVICE_UNITS[@]}"; do
    systemctl --user is-active --quiet "$unit" || { printf 'ERROR: CE service failed post-install verification: %s\\n' "$unit" >&2; return 1; }
    printf '  ✓ %s active\\n' "$unit"
  done
}
audit_runtime_links() {
  local tree="$1" scope="${2:-all}" link resolved git_root
  [[ -d "$tree" ]] || return 0
  while IFS= read -r -d '' link; do
    resolved="$(readlink -f -- "$link" 2>/dev/null || true)"
    if [[ -z "$resolved" ]]; then
      printf 'ERROR: Broken symlink: %s\\n' "$link" >&2
      return 1
    fi
    git_root="$(git -C "$(dirname -- "$resolved")" rev-parse --show-toplevel 2>/dev/null || true)"
    if [[ -n "$git_root" && "$resolved" == "$git_root/"* && "$git_root" != "$DEPLOY_ROOT" ]]; then
      if [[ "$scope" == all || "$(basename -- "$git_root")" == "claude-ensemble" ]]; then
        printf 'ERROR: Symlink points into a Git checkout: %s -> %s (repo: %s)\\n' "$link" "$resolved" "$git_root" >&2
        return 1
      fi
    fi
  done < <(find "$tree" -type l -print0)
}

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

printf '\\nStopping Claude Ensemble services before deployment update...\\n'
stop_ce_services
if [[ -e "$DEPLOY_ROOT" ]]; then mv -- "$DEPLOY_ROOT" "$BACKUP_ROOT"; fi
if ! mv -- "$STAGING" "$DEPLOY_ROOT"; then
  if [[ -e "$BACKUP_ROOT" ]]; then mv -- "$BACKUP_ROOT" "$DEPLOY_ROOT"; fi
  restore_preinstall_services
  printf 'ERROR: Could not activate staged deployment.\\n' >&2
  exit 1
fi
printf '\nInstalling Claude Ensemble from independent deployment: %s\n\n' "$DEPLOY_ROOT"
if bash "$DEPLOY_ROOT/install.sh" "$DEPLOY_ROOT"; then
  :
else
  status=$?
  printf '\\nERROR: CE installation failed (exit %s). Restoring the prior deployment when available.\\n' "$status" >&2
  rm -rf -- "$DEPLOY_ROOT"
  if [[ -e "$BACKUP_ROOT" ]]; then mv -- "$BACKUP_ROOT" "$DEPLOY_ROOT"; fi
  restore_preinstall_services
  exit "$status"
fi

# Component installers can start units before the last configuration steps finish.
# Stop them again so the definitive start follows the completed install and link audit.
stop_ce_services
if ! audit_runtime_links "$DEPLOY_ROOT" all || ! audit_runtime_links "$HOME/.claude" ce-only; then
  printf 'ERROR: Symlink audit failed; restoring prior deployment when available.\\n' >&2
  rm -rf -- "$DEPLOY_ROOT"
  if [[ -e "$BACKUP_ROOT" ]]; then mv -- "$BACKUP_ROOT" "$DEPLOY_ROOT"; fi
  restore_preinstall_services
  exit 1
fi
if [[ -e "$DEPLOY_ROOT/.git" || -L "$DEPLOY_ROOT/.git" ]]; then
  printf 'ERROR: Deployed runtime unexpectedly contains .git metadata.\\n' >&2
  rm -rf -- "$DEPLOY_ROOT"
  if [[ -e "$BACKUP_ROOT" ]]; then mv -- "$BACKUP_ROOT" "$DEPLOY_ROOT"; fi
  restore_preinstall_services
  exit 1
fi

printf '\\nStarting and verifying Claude Ensemble services after installation...\\n'
if ! start_and_verify_services; then
  rm -rf -- "$DEPLOY_ROOT"
  if [[ -e "$BACKUP_ROOT" ]]; then mv -- "$BACKUP_ROOT" "$DEPLOY_ROOT"; fi
  restore_preinstall_services
  exit 1
fi
if [[ -e "$BACKUP_ROOT" ]]; then rm -rf -- "$BACKUP_ROOT"; fi
printf '\\nIndependent CE deployment installed at %s\\n' "$DEPLOY_ROOT"
printf 'Symlink audit passed: no links into Git checkouts under deployment or ~/.claude.\\n'
printf 'All seven Claude Ensemble services are active.\\n'
printf 'This deployment is separate from your working Git checkout.\\n'
