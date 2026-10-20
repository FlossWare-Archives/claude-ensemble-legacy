#!/usr/bin/env bash
set -Eeuo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
TMP="$(mktemp -d)"
trap 'rm -rf -- "$TMP"' EXIT
HOME="$TMP/home"
export HOME
SOURCE="$HOME/source"
mkdir -p "$SOURCE/tools"
cp "$REPO_ROOT/tools/resolve-ce-version.sh" "$SOURCE/tools/"
cp "$REPO_ROOT/tools/create-ce-release-tag.sh" "$SOURCE/tools/"
git -C "$SOURCE" init -q -b main
git -C "$SOURCE" config user.name "Release Test"
git -C "$SOURCE" config user.email "release-test@example.invalid"
git -C "$SOURCE" add tools
git -C "$SOURCE" commit -qm "fixture"
git init -q --bare "$HOME/origin.git"
git -C "$SOURCE" remote add origin "$HOME/origin.git"
git -C "$SOURCE" push -qu origin main
git -C "$SOURCE" branch --set-upstream-to=origin/main main >/dev/null
COMMIT="$(git -C "$SOURCE" rev-parse HEAD)"

output="$(bash "$SOURCE/tools/create-ce-release-tag.sh" --repo "$SOURCE")"
grep -Fxq 'Release tag created: 0.1' <<<"$output"
[[ "$(git -C "$SOURCE" cat-file -t refs/tags/0.1)" == "tag" ]]
[[ "$(git -C "$SOURCE" rev-parse '0.1^{commit}')" == "$COMMIT" ]]
grep -Fq 'Tag is local only.' <<<"$output"

# Dirty trees and non-main branches must fail without creating a tag.
touch "$SOURCE/untracked-file"
if bash "$SOURCE/tools/create-ce-release-tag.sh" --repo "$SOURCE" >/dev/null 2>&1; then
  printf 'ERROR: release tag creation accepted a dirty working tree\n' >&2
  exit 1
fi
rm "$SOURCE/untracked-file"
git -C "$SOURCE" checkout -qb feature/test
if bash "$SOURCE/tools/create-ce-release-tag.sh" --repo "$SOURCE" >/dev/null 2>&1; then
  printf 'ERROR: release tag creation accepted a non-main branch\n' >&2
  exit 1
fi
git -C "$SOURCE" checkout -q main

# A clean but ahead-of-upstream main must also fail closed.
printf 'unreleased change\n' > "$SOURCE/unreleased.txt"
git -C "$SOURCE" add unreleased.txt
git -C "$SOURCE" commit -qm "ahead of upstream"
if bash "$SOURCE/tools/create-ce-release-tag.sh" --repo "$SOURCE" >/dev/null 2>&1; then
  printf 'ERROR: release tag creation accepted main ahead of upstream\n' >&2
  exit 1
fi
git -C "$SOURCE" reset --hard -q origin/main
[[ ! -e "$SOURCE/unreleased.txt" ]]

# Ambiguous numeric dotted tags must fail closed.
git -C "$SOURCE" tag 1.2.3
if bash "$SOURCE/tools/create-ce-release-tag.sh" --repo "$SOURCE" >/dev/null 2>&1; then
  printf 'ERROR: release tag creation ignored an ambiguous numeric tag\n' >&2
  exit 1
fi
git -C "$SOURCE" tag -d 1.2.3 >/dev/null
output2="$(bash "$SOURCE/tools/create-ce-release-tag.sh" --repo "$SOURCE")"
grep -Fxq 'Release tag created: 0.2' <<<"$output2"
[[ "$(git -C "$SOURCE" cat-file -t refs/tags/0.2)" == "tag" ]]

printf 'PASS: validated annotated release tags, clean main requirement, and fail-closed ambiguity handling\n'
