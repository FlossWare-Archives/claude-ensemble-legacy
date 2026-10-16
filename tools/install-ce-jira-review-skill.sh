#!/usr/bin/env bash
# Safely install the CE Jira review skill into the user's global Claude Code profile.
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
SOURCE="$REPO_ROOT/claude-code-skills/ce-jira-review/SKILL.md"
DEST_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills/ce-jira-review"
DEST="$DEST_DIR/SKILL.md"

if [[ ! -f "$SOURCE" ]]; then
  printf 'ERROR: Skill source is missing: %s\n' "$SOURCE" >&2
  exit 1
fi

mkdir -p "$DEST_DIR"
if [[ -e "$DEST" || -L "$DEST" ]]; then
  if [[ -L "$DEST" && "$(readlink -f -- "$DEST")" == "$(readlink -f -- "$SOURCE")" ]]; then
    printf 'CE Jira review skill already installed: %s\n' "$DEST"
    exit 0
  fi
  printf 'ERROR: Refusing to overwrite existing skill: %s\n' "$DEST" >&2
  printf 'Review or back up that file manually before replacing it. No existing content was changed.\n' >&2
  exit 2
fi

ln -s "$SOURCE" "$DEST"
printf 'Installed CE Jira review skill: %s\n' "$DEST"
printf 'Restart Claude Code, then invoke /ce-jira-review from the target repository.\n'
