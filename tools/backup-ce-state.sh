#!/usr/bin/env bash
# Create a durable, private snapshot before CE installation or upgrade.
# Usage: tools/backup-ce-state.sh REASON [deployment-root] [profile-directory]
set -Eeuo pipefail
if [[ "$#" -lt 1 || "$#" -gt 3 || ! "$1" =~ ^[a-zA-Z0-9._-]+$ ]]; then
  printf 'Usage: %s REASON [deployment-root] [profile-directory]\n' "$0" >&2
  exit 2
fi
REASON="$1"
HOME_REAL="$(cd -- "$HOME" && pwd -P)"
ROOT="${2:-${ENSEMBLE_INSTALL_ROOT:-$HOME/.FlossWare/claude-ensemble}}"
PROFILE_DIR="${3:-}"
case "$ROOT" in "~") ROOT="$HOME";; "~/"*) ROOT="$HOME/${ROOT#\~/}";; esac
ROOT="${ROOT%/}"
command -v python3 >/dev/null 2>&1 || { printf 'ERROR: python3 is required for safe backup path validation.\n' >&2; exit 1; }
[[ ! -L "$ROOT" ]] || { printf 'ERROR: refusing to back up a symlinked deployment root: %s\n' "$ROOT" >&2; exit 1; }
ROOT="$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$ROOT")"
case "$ROOT" in "$HOME_REAL"/*) ;; *) printf 'ERROR: deployment root must be beneath HOME: %s\n' "$ROOT" >&2; exit 1;; esac
FLOSSWARE_HOME="$HOME/.FlossWare"
[[ ! -L "$FLOSSWARE_HOME" ]] || { printf 'ERROR: refusing symlinked FlossWare home: %s\n' "$FLOSSWARE_HOME" >&2; exit 1; }
BACKUP_BASE="$FLOSSWARE_HOME/claude-ensemble-backups"
[[ ! -L "$BACKUP_BASE" ]] || { printf 'ERROR: backup directory must not be a symlink: %s\n' "$BACKUP_BASE" >&2; exit 1; }
mkdir -p -- "$BACKUP_BASE"
BACKUP_BASE="$(cd -- "$BACKUP_BASE" && pwd -P)"
case "$BACKUP_BASE" in "$HOME_REAL"/*) ;; *) printf 'ERROR: backup directory resolves outside HOME.\n' >&2; exit 1;; esac
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP="$BACKUP_BASE/$STAMP-$REASON-$$"
mkdir -m 700 -- "$BACKUP"
cleanup_failed() { printf 'ERROR: backup was incomplete; inspect and retain %s. No install/upgrade should continue.\n' "$BACKUP" >&2; }
trap cleanup_failed ERR
mkdir -m 700 "$BACKUP/files"
manifest="$BACKUP/manifest.txt"
{
  printf 'reason=%s\n' "$REASON"
  printf 'created_at_utc=%s\n' "$STAMP"
  printf 'deployment_root=%s\n' "$ROOT"
  printf 'backup_path=%s\n' "$BACKUP"
} > "$manifest"
if [[ -e "$ROOT" ]]; then
  [[ -d "$ROOT" ]] || { printf 'ERROR: existing deployment root is not a directory: %s\n' "$ROOT" >&2; exit 1; }
  cp -a -- "$ROOT" "$BACKUP/files/deployment-root"
  printf 'deployment_root_snapshot=files/deployment-root\n' >> "$manifest"
else
  printf 'deployment_root_snapshot=absent-before-operation\n' >> "$manifest"
fi
LEGACY_PROFILE="$FLOSSWARE_HOME/claude-ensemble-profile"
if [[ -e "$LEGACY_PROFILE" || -L "$LEGACY_PROFILE" ]]; then
  cp -a -- "$LEGACY_PROFILE" "$BACKUP/files/legacy-claude-ensemble-profile"
  printf 'legacy_profile_snapshot=files/legacy-claude-ensemble-profile\n' >> "$manifest"
else
  printf 'legacy_profile_snapshot=absent-before-operation\n' >> "$manifest"
fi
STATE_ROOT="${ENSEMBLE_CE_DEV_STATE:-$HOME/.FlossWare/claude-ensemble-state}"
[[ ! -L "$STATE_ROOT" ]] || { printf 'ERROR: refusing to snapshot a symlinked CE state root: %s\n' "$STATE_ROOT" >&2; exit 1; }
STATE_ROOT="$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$STATE_ROOT")"
case "$STATE_ROOT" in "$HOME_REAL"/*) ;; *) printf 'ERROR: CE state root must resolve beneath HOME: %s\n' "$STATE_ROOT" >&2; exit 1;; esac
if [[ -e "$STATE_ROOT" ]]; then
  [[ -d "$STATE_ROOT" ]] || { printf 'ERROR: CE state root is not a directory: %s\n' "$STATE_ROOT" >&2; exit 1; }
  cp -a -- "$STATE_ROOT" "$BACKUP/files/ce-state-root"
  printf 'ce_state_root_snapshot=files/ce-state-root\nce_state_root_path=%s\n' "$STATE_ROOT" >> "$manifest"
else
  printf 'ce_state_root_snapshot=absent-before-operation\nce_state_root_path=%s\n' "$STATE_ROOT" >> "$manifest"
fi
UNIT_DIR="${ENSEMBLE_USER_UNIT_DIR:-$HOME/.config/systemd/user}"
mkdir -p "$BACKUP/files/systemd-user-units"
unit_count=0
for unit in claude-memory.service claude-thompson.service claude-learning.service claude-alert.service claude-messenger.service claude-graph.service claude-ensemble.service; do
  if [[ -e "$UNIT_DIR/$unit" || -L "$UNIT_DIR/$unit" ]]; then
    cp -a -- "$UNIT_DIR/$unit" "$BACKUP/files/systemd-user-units/$unit"
    printf 'unit_snapshot=%s\n' "$unit" >> "$manifest"
    unit_count=$((unit_count + 1))
  fi
done
printf 'managed_unit_count=%s\n' "$unit_count" >> "$manifest"
chmod 600 "$manifest"
# Verify the snapshot can be read before allowing installation to proceed.
[[ -r "$manifest" && -d "$BACKUP/files" ]]
if [[ -e "$BACKUP/files/deployment-root" ]]; then
  [[ -d "$BACKUP/files/deployment-root" ]] || exit 1
fi
if [[ -n "$PROFILE_DIR" ]]; then
  [[ ! -L "$PROFILE_DIR" ]] || { printf 'ERROR: refusing to snapshot a symlinked profile directory: %s\n' "$PROFILE_DIR" >&2; exit 1; }
  PROFILE_DIR="$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$PROFILE_DIR")"
  case "$PROFILE_DIR" in "$HOME_REAL"/*) ;; *) printf 'ERROR: selected profile must resolve beneath HOME: %s\n' "$PROFILE_DIR" >&2; exit 1;; esac
  if [[ -d "$PROFILE_DIR" ]]; then
    cp -a -- "$PROFILE_DIR" "$BACKUP/files/selected-profile"
    printf 'selected_profile_path=%s\nselected_profile_snapshot=files/selected-profile\n' "$PROFILE_DIR" >> "$manifest"
  else
    printf 'selected_profile_path=%s\nselected_profile_snapshot=absent-before-operation\n' "$PROFILE_DIR" >> "$manifest"
  fi
fi
trap - ERR
printf 'Pre-operation backup created: %s\n' "$BACKUP"
printf 'Backup contains the existing deployment tree (if any), legacy CE profile and development state (if present), the selected profile (when supplied), and existing CE-managed user service units.\n'
