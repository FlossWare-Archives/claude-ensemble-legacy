#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
SELECTOR="$ROOT/tools/select-ce-release-version.sh"
TMP="$(mktemp -d)"
trap 'rm -rf -- "$TMP"' EXIT

git init -q --bare "$TMP/origin.git"
git init -q "$TMP/work"
git -C "$TMP/work" config user.name Test
git -C "$TMP/work" config user.email test@example.invalid
git -C "$TMP/work" commit --allow-empty -qm initial
git -C "$TMP/work" tag 0.1
git -C "$TMP/work" tag 0.9
git -C "$TMP/work" tag 0.10
git -C "$TMP/work" remote add origin "$TMP/origin.git"
git -C "$TMP/work" push -q origin main 2>/dev/null || git -C "$TMP/work" push -q origin HEAD:main
git -C "$TMP/work" push -q origin --tags

actual="$(bash "$SELECTOR" --remote "$TMP/origin.git")"
[[ "$actual" == "0.10" ]] || { printf 'Expected 0.10, got %s\n' "$actual" >&2; exit 1; }

git -C "$TMP/work" tag 1.2.3
git -C "$TMP/work" push -q origin --tags
if bash "$SELECTOR" --remote "$TMP/origin.git" >"$TMP/out" 2>"$TMP/err"; then
  printf 'ERROR: selector accepted ambiguous numeric dotted tag\n' >&2
  exit 1
fi
grep -Fq 'ambiguous' "$TMP/err"

git init -q --bare "$TMP/empty.git"
if bash "$SELECTOR" --remote "$TMP/empty.git" >"$TMP/out" 2>"$TMP/err"; then
  printf 'ERROR: selector accepted a remote with no release tags\n' >&2
  exit 1
fi
grep -Fq 'No MAJOR.MINOR release tags found' "$TMP/err"

printf 'PASS: latest release selection, numeric ordering, and fail-closed tag validation\n'
