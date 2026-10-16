#!/usr/bin/env bash
# End-to-end acceptance test for Claude Code -> Vertex -> CE worker/arbiter.
# Uses a disposable Git repository and never reads or modifies project source.
set -euo pipefail

CE_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_REPO="$(mktemp -d "${TMPDIR:-/tmp}/ce-vertex-review.XXXXXX")"
trap 'rm -rf "$TMP_REPO"' EXIT

git -C "$TMP_REPO" init -b main >/dev/null
git -C "$TMP_REPO" config user.name "CE Vertex Smoke Test"
git -C "$TMP_REPO" config user.email "ce-vertex-smoke@example.invalid"

cat > "$TMP_REPO/report.py" <<'PY'
def first(values):
    return values[0] if values else None
PY

git -C "$TMP_REPO" add report.py
git -C "$TMP_REPO" commit -m "safe baseline" >/dev/null

cat > "$TMP_REPO/report.py" <<'PY'
def first(values):
    return values[0]
PY

echo "Running live Vertex review against a disposable fixture."
echo "This makes three Claude Code invocations (two workers and one arbiter)."
python3 "$CE_ROOT/tools/arbitrate.py" code-review "$TMP_REPO/report.py" \
  --target-branch main --phases 1 --tiers 1 | tee "$TMP_REPO/review.out"

grep -q "Adjudicated result:" "$TMP_REPO/review.out" || {
  echo "FAIL: CE produced no adjudicated review report." >&2
  exit 1
}
grep -q "Failed workers: 0" "$TMP_REPO/review.out" || {
  echo "FAIL: at least one worker failed; review is not accepted." >&2
  exit 1
}

echo "PASS: Vertex worker -> arbiter review completed with zero failed workers."
