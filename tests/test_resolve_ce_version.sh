#!/usr/bin/env bash
set -Eeuo pipefail
REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
RESOLVER="$REPO_ROOT/tools/resolve-ce-version.sh"
TMP="$(mktemp -d)"
trap 'rm -rf -- "$TMP"' EXIT

make_repo() {
  local repo="$1"
  mkdir -p "$repo"
  git -C "$repo" init -q
  git -C "$repo" -c user.name=Test -c user.email=test@example.invalid commit --allow-empty -qm init
}
expect_version() {
  local expected="$1"
  shift
  local actual
  actual="$(bash "$RESOLVER" --repo "$@" 2>/dev/null)"
  [[ "$actual" == "$expected" ]] || {
    printf 'Expected version %s, got %s\n' "$expected" "$actual" >&2
    exit 1
  }
}
expect_failure() {
  local repo="$1"
  shift
  if bash "$RESOLVER" --repo "$repo" "$@" >"$TMP/stdout" 2>"$TMP/stderr"; then
    printf 'Expected resolver failure for %s\n' "$repo" >&2
    exit 1
  fi
}

REPO="$TMP/repo"
make_repo "$REPO"
expect_version 0.1 "$REPO"
git -C "$REPO" tag 0.1
expect_version 0.2 "$REPO"
git -C "$REPO" tag 0.2
expect_version 0.3 "$REPO"
git -C "$REPO" tag 0.9
expect_version 0.10 "$REPO"
git -C "$REPO" tag 1.0
expect_version 1.1 "$REPO"

git -C "$REPO" tag 1.1.1
expect_failure "$REPO"

REPO2="$TMP/repo2"
make_repo "$REPO2"
git -C "$REPO2" tag 0.1
DEPLOY="$TMP/deploy"
mkdir -p "$DEPLOY/releases/0.2"
expect_failure "$REPO2" --deployment-root "$DEPLOY"
printf 'PASS: automatic MAJOR.MINOR resolution and fail-closed release-state validation\n'
