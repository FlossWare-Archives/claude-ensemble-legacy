#!/usr/bin/env bash
# Install the complete CE slash-command skill set into one selected Claude Code profile.
# Existing user-owned skills are preserved; a conflicting skill is skipped and reported.
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PROFILE_DIR="${ENSEMBLE_CLAUDE_CODE_CONFIG_DIR:-${CLAUDE_CONFIG_DIR:-$HOME/.claude}}"
case "$PROFILE_DIR" in
  "~") PROFILE_DIR="$HOME" ;;
  "~/"*) PROFILE_DIR="$HOME/${PROFILE_DIR#\~/}" ;;
esac

if [[ -L "$PROFILE_DIR" || -L "$PROFILE_DIR/skills" ]]; then
  printf 'WARNING: Refusing to install CE skills through a symlinked profile or skills directory: %s\\n' "$PROFILE_DIR" >&2
  printf 'No CE skills were installed; review the symlink target and install manually if intended.\\n' >&2
  exit 0
fi

skills=(review jira-review status diagnose memory learning ga solve)
conflicts=0
for name in "${skills[@]}"; do
  installer="$SCRIPT_DIR/install-ce-$name-skill.sh"
  if [[ ! -f "$installer" ]]; then
    printf 'ERROR: Missing CE skill installer: %s\n' "$installer" >&2
    exit 1
  fi
  printf '\n=== Installing /ce-%s into %s ===\n' "$name" "$PROFILE_DIR"
  if ENSEMBLE_CLAUDE_CODE_CONFIG_DIR="$PROFILE_DIR" CLAUDE_CONFIG_DIR="$PROFILE_DIR" bash "$installer"; then
    :
  else
    status=$?
    if [[ "$status" -eq 2 ]]; then
      conflicts=$((conflicts + 1))
      printf 'WARNING: Preserved conflicting user-owned skill /ce-%s.\n' "$name" >&2
    else
      exit "$status"
    fi
  fi
done

printf '\nCE skill installation complete. Conflicting skills preserved: %s\n' "$conflicts"
if [[ "$conflicts" -gt 0 ]]; then
  printf 'Review the warnings above; no existing skill content was overwritten.\n'
fi
