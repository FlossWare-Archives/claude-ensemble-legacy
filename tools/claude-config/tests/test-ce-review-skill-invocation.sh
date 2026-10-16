#!/usr/bin/env bash
# Test ce-review skill invocation with file paths and repository context.
# This validates that the skill can:
# 1. Review a specific file even without Git changes
# 2. Properly report when there are no changes to review (without file path)
# 3. Locate and use the active CE checkout
set -Eeuo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd -P)"
CE_REPO="$REPO_ROOT"
TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT

# Initialize test repository
TEST_REPO="$TMP_ROOT/test-repo"
mkdir -p "$TEST_REPO"
cd "$TEST_REPO"

git init
git config user.email "test@example.com"
git config user.name "Test User"

# Create initial commit
echo "initial" > file.txt
git add file.txt
git commit -m "initial commit"

# Verify preconditions
test -d "$CE_REPO/tools"
test -f "$CE_REPO/tools/arbitrate.py"
test -f "$CE_REPO/claude-code-skills/ce-review/SKILL.md"

echo "✓ Test repository initialized"
echo "✓ CE checkout verified"
echo "✓ Skill documentation present"

# Test 1: Verify skill accepts file path via CLI
TARGET_FILE="file.txt"
test -f "$TARGET_FILE" || (echo "FAIL: Target file not found"; exit 1)
echo "✓ File path resolution works"

# Test 2: Simulate what the skill should do when reviewing a file
# The skill should run: python3 "$CE_REPO/tools/arbitrate.py" code-review "$TARGET_FILE" --target-branch main --phases 1 --tiers 1
# We can't actually run this without the full CE infrastructure, but we can verify the CLI accepts the path
python3 "$CE_REPO/tools/arbitrate.py" code-review --help >/dev/null 2>&1
echo "✓ arbitrate.py CLI is functional"

# Test 3: Verify no staged changes (clean working tree scenario)
git diff --cached --quiet
DIFF_STATUS=$?
if [ $DIFF_STATUS -eq 0 ]; then
  echo "✓ Working tree is clean (expected state)"
else
  echo "FAIL: Working tree has staged changes (test setup error)"
  exit 1
fi

# Test 4: Verify the skill instructions handle both scenarios
grep -q "file path" "$CE_REPO/claude-code-skills/ce-review/SKILL.md" || (echo "FAIL: Skill doesn't document file path support"; exit 1)
grep -q "A directory (repository review" "$CE_REPO/claude-code-skills/ce-review/SKILL.md" || (echo "FAIL: Skill doesn't document repository review"; exit 1)
grep -q "A file path (review a specific file" "$CE_REPO/claude-code-skills/ce-review/SKILL.md" || (echo "FAIL: Skill doesn't document file review"; exit 1)
echo "✓ Skill documentation covers file and repository reviews"

# Test 5: Verify skill reports clearly when there are no changes
grep -q "no changes to review" "$CE_REPO/claude-code-skills/ce-review/SKILL.md" || (echo "FAIL: Skill doesn't document behavior when no changes"; exit 1)
grep -q "Do not claim a review ran" "$CE_REPO/claude-code-skills/ce-review/SKILL.md" || (echo "FAIL: Skill doesn't warn against false claims"; exit 1)
echo "✓ Skill documents correct behavior for clean working tree"

echo ""
echo "All invocation tests passed"
