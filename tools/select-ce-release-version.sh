#!/usr/bin/env bash
# Select the newest published MAJOR.MINOR tag without creating or changing refs.
# A release must be tagged and pushed explicitly before the default installer
# can select it.
set -Eeuo pipefail

REMOTE="https://github.com/FlossWare/claude-ensemble.git"
if [[ "${1:-}" == "--remote" && $# -eq 2 ]]; then
  REMOTE="$2"
elif [[ $# -ne 0 ]]; then
  printf 'Usage: %s [--remote git-url]\n' "$0" >&2
  exit 2
fi

command -v git >/dev/null 2>&1 || { printf 'ERROR: git is required to select a release.\n' >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { printf 'ERROR: python3 is required to select a release.\n' >&2; exit 1; }

if ! TAGS="$(git ls-remote --tags --refs "$REMOTE")"; then
  printf 'ERROR: Unable to read release tags from %s\n' "$REMOTE" >&2
  exit 1
fi

python3 - "$REMOTE" "$TAGS" <<'PY'
import re
import sys

remote = sys.argv[1]
lines = sys.argv[2].splitlines()
versions = []
ambiguous = []
for line in lines:
    fields = line.split()
    if len(fields) != 2:
        continue
    name = fields[1].removeprefix("refs/tags/")
    if re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", name):
        ambiguous.append(name)
    elif re.fullmatch(r"[0-9]+\.[0-9]+", name):
        major, minor = map(int, name.split("."))
        versions.append((major, minor, name))
if ambiguous:
    print("ERROR: Numeric dotted tags with patch components are ambiguous: " + ", ".join(sorted(ambiguous)), file=sys.stderr)
    raise SystemExit(1)
if not versions:
    print(f"ERROR: No MAJOR.MINOR release tags found at {remote}. Create and push a validated release tag first.", file=sys.stderr)
    raise SystemExit(1)
print(max(versions)[2])
PY
