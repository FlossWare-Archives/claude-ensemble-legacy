#!/usr/bin/env bash
# Integration tests for CE skill installers. Uses a temporary HOME and never touches the real profile.
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
TMP_ROOT="$(mktemp -d)"
trap 'rm -rf -- "$TMP_ROOT"' EXIT

export HOME="$TMP_ROOT/home"
mkdir -p "$HOME"
export ENSEMBLE_CLAUDE_CODE_CONFIG_DIR="$HOME/.config/claude-ensemble/claude-profile"

for name in review jira-review status diagnose memory learning ga solve; do
  installer="$ROOT/tools/install-ce-$name-skill.sh"
  source_file="$ROOT/claude-code-skills/ce-$name/SKILL.md"
  destination="$ENSEMBLE_CLAUDE_CODE_CONFIG_DIR/skills/ce-$name/SKILL.md"

  [[ -f "$installer" ]] || { printf 'missing installer: %s\n' "$installer" >&2; exit 1; }
  [[ -f "$source_file" ]] || { printf 'missing skill source: %s\n' "$source_file" >&2; exit 1; }

  bash "$installer"
  [[ -f "$destination" ]]
  cmp -s -- "$source_file" "$destination"
  [[ "$(stat -c '%a' "$destination")" == "600" ]]

  # A second install must be idempotent.
  bash "$installer"
  cmp -s -- "$source_file" "$destination"
done

# An existing different skill must not be overwritten.
CONFLICT_PROFILE="$TMP_ROOT/conflict-profile"
export ENSEMBLE_CLAUDE_CODE_CONFIG_DIR="$CONFLICT_PROFILE"
mkdir -p "$CONFLICT_PROFILE/skills/ce-status"
printf '%s\n' 'user-owned skill' > "$CONFLICT_PROFILE/skills/ce-status/SKILL.md"
if bash "$ROOT/tools/install-ce-status-skill.sh"; then
  printf 'installer unexpectedly overwrote a conflicting skill\n' >&2
  exit 1
else
  status=$?
  [[ "$status" -eq 2 ]] || { printf 'unexpected conflict exit status: %s\n' "$status" >&2; exit 1; }
fi
[[ "$(cat "$CONFLICT_PROFILE/skills/ce-status/SKILL.md")" == 'user-owned skill' ]]

# A symlinked skills directory must never receive writes, including through the aggregate installer.
SYMLINK_PROFILE="$TMP_ROOT/symlink-profile"
SYMLINK_SKILLS="$TMP_ROOT/real-skills"
mkdir -p "$SYMLINK_PROFILE" "$SYMLINK_SKILLS"
ln -s "$SYMLINK_SKILLS" "$SYMLINK_PROFILE/skills"
ENSEMBLE_CLAUDE_CODE_CONFIG_DIR="$SYMLINK_PROFILE" bash "$ROOT/tools/install-ce-skills.sh"
[[ -z "$(find "$SYMLINK_SKILLS" -mindepth 1 -print -quit)" ]]

# The aggregate installer must be idempotent and install every supported CE skill.
export ENSEMBLE_CLAUDE_CODE_CONFIG_DIR="$HOME/.config/claude-ensemble/claude-profile"
bash "$ROOT/tools/install-ce-skills.sh"
for name in review jira-review status diagnose memory learning ga solve; do
  [[ -f "$ENSEMBLE_CLAUDE_CODE_CONFIG_DIR/skills/ce-$name/SKILL.md" ]]
done

printf 'All CE skill installer tests passed.\n'
