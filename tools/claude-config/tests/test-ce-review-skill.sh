#!/usr/bin/env bash
set -Eeuo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd -P)"
INSTALLER="$REPO_ROOT/tools/install-ce-review-skill.sh"
TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT

PROFILE="$TMP_ROOT/profile"
HOME="$TMP_ROOT/home"
mkdir -p "$HOME"

ENSEMBLE_CLAUDE_CODE_CONFIG_DIR="$PROFILE" HOME="$HOME" bash "$INSTALLER"
DEST="$PROFILE/skills/ce-review/SKILL.md"
test -f "$DEST"
cmp -s "$REPO_ROOT/claude-code-skills/ce-review/SKILL.md" "$DEST"

ENSEMBLE_CLAUDE_CODE_CONFIG_DIR="$PROFILE" HOME="$HOME" bash "$INSTALLER"
cmp -s "$REPO_ROOT/claude-code-skills/ce-review/SKILL.md" "$DEST"

printf 'user-owned skill\n' > "$DEST"
set +e
ENSEMBLE_CLAUDE_CODE_CONFIG_DIR="$PROFILE" HOME="$HOME" bash "$INSTALLER" >/dev/null 2>&1
status=$?
set -e
test "$status" -eq 2
test "$(cat "$DEST")" = "user-owned skill"

echo "CE code review skill installer tests passed"
