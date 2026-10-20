#!/usr/bin/env bash
# Resolve the next unused MAJOR.MINOR release label without changing repository state.
# Usage: tools/resolve-ce-version.sh [--repo CHECKOUT] [--deployment-root ROOT]
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
DEFAULT_REPO="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
REPO="$DEFAULT_REPO"
DEPLOYMENT_ROOT=""

die() { printf 'ERROR: %s\n' "$*" >&2; exit 2; }
usage() {
  printf 'Usage: %s [--repo CHECKOUT] [--deployment-root ROOT]\n' "$0" >&2
  exit 2
}

while (($#)); do
  case "$1" in
    --repo)
      (($# >= 2)) || usage
      REPO="$2"
      shift 2
      ;;
    --deployment-root)
      (($# >= 2)) || usage
      DEPLOYMENT_ROOT="$2"
      shift 2
      ;;
    -h|--help) usage ;;
    *) die "unknown argument: $1" ;;
  esac
done

[[ -d "$REPO" ]] || die "repository directory does not exist: $REPO"
git -C "$REPO" rev-parse --git-dir >/dev/null 2>&1 || die "not a Git checkout: $REPO"
command -v python3 >/dev/null 2>&1 || die "python3 is required"

# Python handles arbitrary-length numeric components without shell integer overflow.
# Only exact MAJOR.MINOR tags are release tags. Numeric dotted tags with any other
# number of components are ambiguous and must be fixed, not silently skipped.
TAGS="$(git -C "$REPO" tag --list)"
VERSIONS_FILE="$(mktemp)"
trap 'rm -f -- "$VERSIONS_FILE"' EXIT
printf '%s\n' "$TAGS" > "$VERSIONS_FILE"

python3 - "$VERSIONS_FILE" "$DEPLOYMENT_ROOT" <<'PY'
import os
import re
import sys

tags_file, deployment_root = sys.argv[1:]
version_re = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
numeric_dotted_re = re.compile(r"^[0-9]+(?:\.[0-9]+)+$")
versions = set()

with open(tags_file, encoding="utf-8") as source:
    tags = [line.rstrip("\n") for line in source if line.rstrip("\n")]

for tag in tags:
    if version_re.fullmatch(tag):
        versions.add(tuple(map(int, tag.split("."))))
    elif numeric_dotted_re.fullmatch(tag):
        print(
            f"ERROR: ambiguous numeric release tag {tag!r}; expected MAJOR.MINOR (for example 0.2)",
            file=sys.stderr,
        )
        raise SystemExit(2)

if deployment_root:
    releases_dir = os.path.join(deployment_root, "releases")
    if os.path.lexists(releases_dir):
        if os.path.islink(releases_dir) or not os.path.isdir(releases_dir):
            print(f"ERROR: releases path is not a real directory: {releases_dir}", file=sys.stderr)
            raise SystemExit(2)
        for entry in os.listdir(releases_dir):
            path = os.path.join(releases_dir, entry)
            if not version_re.fullmatch(entry):
                if numeric_dotted_re.fullmatch(entry):
                    print(
                        f"ERROR: ambiguous release directory {entry!r}; expected MAJOR.MINOR",
                        file=sys.stderr,
                    )
                    raise SystemExit(2)
                continue
            if os.path.islink(path) or not os.path.isdir(path):
                print(f"ERROR: release path is not a real directory: {path}", file=sys.stderr)
                raise SystemExit(2)
            candidate = tuple(map(int, entry.split(".")))
            if candidate not in versions and entry != "0.0":
                print(
                    f"ERROR: release directory {entry} has no matching Git tag; reconcile release state before resolving a version",
                    file=sys.stderr,
                )
                raise SystemExit(2)

if not versions:
    print("0.1")
    raise SystemExit(0)

major, minor = max(versions)
candidate = (major, minor + 1)
if deployment_root:
    releases_dir = os.path.join(deployment_root, "releases")
    while os.path.lexists(os.path.join(releases_dir, f"{candidate[0]}.{candidate[1]}")):
        print(
            f"ERROR: next version {candidate[0]}.{candidate[1]} already exists in deployment state; reconcile release state before resolving a version",
            file=sys.stderr,
        )
        raise SystemExit(2)

print(f"{candidate[0]}.{candidate[1]}")
PY
