#!/usr/bin/env bash
# Safely install the CE diagnostics skill into the selected Claude Code profile.
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
SOURCE="$REPO_ROOT/claude-code-skills/ce-diagnose/SKILL.md"
PROFILE_DIR="${ENSEMBLE_CLAUDE_CODE_CONFIG_DIR:-${CLAUDE_CONFIG_DIR:-$HOME/.claude}}"
case "$PROFILE_DIR" in
  "~") PROFILE_DIR="$HOME" ;;
  "~/"*) PROFILE_DIR="$HOME/${PROFILE_DIR#\~/}" ;;
esac
DEST_DIR="$PROFILE_DIR/skills/ce-diagnose"
DEST="$DEST_DIR/SKILL.md"

if [[ ! -f "$SOURCE" ]]; then
  printf 'ERROR: Skill source is missing: %s\n' "$SOURCE" >&2
  exit 1
fi
if [[ -L "$PROFILE_DIR" || -L "$PROFILE_DIR/skills" || -L "$DEST_DIR" ]]; then
  printf 'ERROR: Refusing to install through a symlinked profile or skills directory.\n' >&2
  exit 2
fi
mkdir -p "$DEST_DIR"
if [[ -e "$DEST" || -L "$DEST" ]]; then
  if [[ -f "$DEST" && ! -L "$DEST" ]] && cmp -s -- "$SOURCE" "$DEST"; then
    printf 'CE diagnostics skill is already current: %s\n' "$DEST"
    exit 0
  fi
  printf 'ERROR: Refusing to overwrite existing skill: %s\n' "$DEST" >&2
  printf 'Review or back up that file manually before replacing it. No existing content was changed.\n' >&2
  exit 2
fi
install -m 600 -- "$SOURCE" "$DEST"
printf 'Installed CE diagnostics skill: %s\n' "$DEST"
printf 'Restart Claude Code, then invoke /ce-diagnose.\n'
