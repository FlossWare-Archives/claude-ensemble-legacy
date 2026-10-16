#!/usr/bin/env bash
# Integration test for /ce-review skill: validate file-specific review CLI execution
# Tests that the underlying arbitrate.py code-review CLI works end-to-end with providers.
set -Eeuo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd -P)"
CE_REPO="$REPO_ROOT"
TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT

# Test 1: Verify CLI accepts file path and exits successfully
echo "Test 1: CLI with valid file path"
TIMEOUT=600  # 10 minutes for full review
if timeout $TIMEOUT python3 "$CE_REPO/tools/arbitrate.py" code-review \
    "$CE_REPO/arbitration/orchestrator.py" \
    --target-branch main --phases 1 --tiers 1 > "$TMP_ROOT/review_output.txt" 2>&1
then
    echo "✓ CLI completed successfully (exit 0)"
else
    EXIT_CODE=$?
    if [ $EXIT_CODE -eq 124 ]; then
        echo "✗ FAIL: CLI timeout after ${TIMEOUT}s"
        head -50 "$TMP_ROOT/review_output.txt"
        exit 1
    else
        echo "✗ FAIL: CLI exited with code $EXIT_CODE"
        head -50 "$TMP_ROOT/review_output.txt"
        exit 1
    fi
fi

# Test 2: Verify output is non-empty
OUTPUT_SIZE=$(wc -c < "$TMP_ROOT/review_output.txt")
if [ $OUTPUT_SIZE -lt 100 ]; then
    echo "✗ FAIL: Review output too small ($OUTPUT_SIZE bytes)"
    cat "$TMP_ROOT/review_output.txt"
    exit 1
fi
echo "✓ Review output is $OUTPUT_SIZE bytes"

# Test 3: Verify output contains expected sections
if ! grep -q "MULTI-PHASE ARBITRATION REPORT\|Adjudicated result" "$TMP_ROOT/review_output.txt"; then
    echo "✗ FAIL: Review output missing expected sections"
    tail -50 "$TMP_ROOT/review_output.txt"
    exit 1
fi
echo "✓ Review output contains expected sections"

# Test 4: Verify phase execution logged
if ! grep -q "Starting worker\|Arbiter.*completed" "$TMP_ROOT/review_output.txt"; then
    echo "✗ FAIL: Review output missing phase execution logs"
    cat "$TMP_ROOT/review_output.txt"
    exit 1
fi
echo "✓ Review output shows phase execution"

# Test 5: Verify skill can properly report success/failure
# (This would be tested in the /ce-review skill itself, but we validate the CLI here)
if grep -q "Verdict\|Adjudicated\|Correctness\|Security" "$TMP_ROOT/review_output.txt"; then
    echo "✓ Review output contains analysis and verdict"
else
    echo "✗ FAIL: Review output lacks analysis"
    tail -30 "$TMP_ROOT/review_output.txt"
    exit 1
fi

echo ""
echo "All CLI integration tests passed"
