#!/usr/bin/env bash
# Install a versioned, isolated Claude Ensemble deployment.
#
# Usage:
#   tools/install-ce-versioned.sh MAJOR.MINOR [source-checkout]
#
# This entrypoint intentionally refuses to overwrite an existing unversioned
# deployment. Migration must be explicit because silently moving a live service
# tree is how rollback plans become folklore.
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"

# ~/.FlossWare is the canonical home for CE deployment state. Do not silently
# follow a symlink here: that can relocate staging/releases into a development
# checkout and defeats the intended separation between deployed state and source.
FLOSSWARE_HOME="$HOME/.FlossWare"
if [[ -L "$FLOSSWARE_HOME" ]]; then
  printf 'ERROR: %s must be a real directory, not a symlink. Resolve the path explicitly before installing.\n' "$FLOSSWARE_HOME" >&2
  exit 1
fi

ROOT="${ENSEMBLE_INSTALL_ROOT:-$HOME/.FlossWare/claude-ensemble}"
case "$ROOT" in "~") ROOT="$HOME";; "~/"*) ROOT="$HOME/${ROOT#\~/}";; esac
ROOT="${ROOT%/}"
command -v python3 >/dev/null 2>&1 || {
  printf 'ERROR: python3 is required to validate the deployment path.\n' >&2
  exit 1
}
[[ ! -L "$ROOT" ]] || {
  printf 'ERROR: Install root must not be a symlink: %s\n' "$ROOT" >&2
  exit 1
}
HOME_REAL="$(cd -- "$HOME" && pwd -P)"
ROOT="$(python3 -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' "$ROOT")"
case "$ROOT" in
  "$HOME_REAL"/*) ;;
  *)
    printf 'ERROR: Deployment path must resolve to a child of HOME: %s\n' "$ROOT" >&2
    exit 1
    ;;
esac
ROOT_BASE="$(basename -- "$ROOT")"
[[ -n "$ROOT_BASE" && "$ROOT_BASE" != "." && "$ROOT_BASE" != ".." ]] || {
  printf 'ERROR: Deployment root must name a child directory: %s\n' "$ROOT" >&2
  exit 1
}
ROOT_PARENT="$(dirname -- "$ROOT")"
mkdir -p -- "$ROOT_PARENT"
ROOT_PARENT="$(cd -- "$ROOT_PARENT" && pwd -P)"
ROOT="$ROOT_PARENT/$ROOT_BASE"
case "$ROOT" in
  "$HOME_REAL"/*) ;;
  *)
    printf 'ERROR: Deployment path must resolve to a child of HOME: %s\n' "$ROOT" >&2
    exit 1
    ;;
esac
if [[ "$#" -lt 1 || "$#" -gt 2 || ! "$1" =~ ^[0-9]+\.[0-9]+$ ]]; then
  printf 'Usage: %s MAJOR.MINOR [source-checkout]\n' "$0" >&2
  exit 2
fi
VERSION="$1"
SOURCE_DIR="${2:-}"
STAGING_ONLY="${ENSEMBLE_STAGING_ONLY:-0}"
if [[ "$STAGING_ONLY" != "0" && "$STAGING_ONLY" != "1" ]]; then
  printf 'ERROR: ENSEMBLE_STAGING_ONLY must be 0 or 1.\n' >&2
  exit 2
fi
if [[ "$STAGING_ONLY" == "1" ]]; then
  [[ -n "${ENSEMBLE_INSTALL_ROOT:-}" ]] || {
    printf 'ERROR: Staging-only mode requires an explicit ENSEMBLE_INSTALL_ROOT beneath HOME.\n' >&2
    exit 2
  }
  if [[ -e "$ROOT" && -n "$(find "$ROOT" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]]; then
    printf 'ERROR: Staging-only mode requires a new or empty install root: %s\n' "$ROOT" >&2
    exit 1
  fi
fi
RELEASES="$ROOT/releases"
RELEASE="$RELEASES/$VERSION"
SHARED="$ROOT/shared"
assert_managed_layout() {
  local path
  for path in \
    "$RELEASES" "$SHARED" "$SHARED/claude-code" "$SHARED/config" "$SHARED/data" \
    "$SHARED/data/memory" "$SHARED/data/learning" "$SHARED/data/alerts" \
    "$SHARED/runtime" "$SHARED/logs" "$SHARED/activation-backups"; do
    [[ ! -L "$path" ]] || { printf 'ERROR: managed deployment path must not be a symlink: %s\\n' "$path" >&2; exit 1; }
    [[ ! -e "$path" || -d "$path" ]] || { printf 'ERROR: managed deployment path is not a directory: %s\\n' "$path" >&2; exit 1; }
  done
  path="$SHARED/config/environment"
  [[ ! -L "$path" ]] || { printf 'ERROR: managed service environment must not be a symlink: %s\\n' "$path" >&2; exit 1; }
  [[ ! -e "$path" || -f "$path" ]] || { printf 'ERROR: managed service environment is not a regular file: %s\\n' "$path" >&2; exit 1; }
}
assert_managed_layout
WORK="$(mktemp -d "${TMPDIR:-/tmp}/ce-versioned.XXXXXX")"
STAGED="$WORK/release"
cleanup() { rm -rf -- "$WORK"; }
trap cleanup EXIT

for command in tar curl python3 git; do
  command -v "$command" >/dev/null 2>&1 || { printf 'ERROR: Required command not found: %s\n' "$command" >&2; exit 1; }
done
if [[ "$STAGING_ONLY" != "1" ]]; then
  command -v systemctl >/dev/null 2>&1 || { printf 'ERROR: Required command not found: systemctl\n' >&2; exit 1; }
  systemctl --user show-environment >/dev/null 2>&1 || {
    printf 'ERROR: systemd user manager is unavailable. Run from your logged-in user session.\n' >&2
    exit 1
  }
fi
[[ ! -L "$ROOT" ]] || { printf 'ERROR: Install root must not be a symlink: %s\n' "$ROOT" >&2; exit 1; }
if [[ "$STAGING_ONLY" != "1" ]]; then
  # Persist a complete snapshot before migration, profile import, release creation,
  # or service activation. A failed backup aborts the install/upgrade.
  bash "$SCRIPT_DIR/backup-ce-state.sh" "pre-install-$VERSION" "$ROOT"
fi
LEGACY_MIGRATION=0
LEGACY_SNAPSHOT="$WORK/legacy"
if [[ -e "$ROOT" && ! -d "$RELEASES" ]]; then
  if [[ -e "$ROOT/.git" || -L "$ROOT/.git" ]]; then
    printf 'ERROR: %s contains Git metadata; refusing to migrate a checkout.\n' "$ROOT" >&2
    exit 1
  fi
  if [[ -f "$ROOT/install.sh" ]]; then
    if [[ "$VERSION" == "0.0" ]]; then
      printf 'ERROR: Version 0.0 is reserved for the automatic legacy rollback snapshot.\n' >&2
      exit 1
    fi
    mkdir -p "$LEGACY_SNAPSHOT"
    tar -C "$ROOT" -cf - . | tar -C "$LEGACY_SNAPSHOT" -xf -
    LEGACY_MIGRATION=1
  elif [[ -n "$(find "$ROOT" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]]; then
    printf 'ERROR: Existing non-versioned files found under %s; refusing ambiguous migration.\n' "$ROOT" >&2
    exit 1
  fi
fi
[[ ! -e "$RELEASE" && ! -L "$RELEASE" ]] || {
  printf 'ERROR: Release already exists: %s\n' "$RELEASE" >&2
  printf 'Releases are immutable. Choose a new MAJOR.MINOR version.\n' >&2
  exit 1
}

mkdir -p "$RELEASES" "$SHARED/claude-code" "$SHARED/config" "$SHARED/data" "$SHARED/logs"
assert_managed_layout
if [[ "$LEGACY_MIGRATION" == "1" ]]; then
  mkdir -p "$RELEASES/0.0"
  tar -C "$LEGACY_SNAPSHOT" -cf - . | tar -C "$RELEASES/0.0" -xf -
  rm -rf -- "$RELEASES/0.0/claude-code"
  ln -s ../../shared/claude-code "$RELEASES/0.0/claude-code"
  ln -s releases/0.0 "$ROOT/current"
  printf 'Preserved the previous standalone deployment as rollback release 0.0.\n'
fi
# Import legacy CE profile files without overwriting the shared profile. Keep the
# source directories until activation succeeds so a failed install is reversible.
# Staging-only mode intentionally imports no user profile data.
if [[ "$STAGING_ONLY" != "1" ]]; then
  for legacy_profile in "$ROOT/claude-code" "$HOME/.FlossWare/claude-ensemble-profile"; do
    if [[ -L "$legacy_profile" ]]; then
      printf 'ERROR: Refusing to migrate symlinked profile directory: %s\n' "$legacy_profile" >&2
      exit 1
    fi
    if [[ -d "$legacy_profile" ]]; then
      cp -a --update=none -- "$legacy_profile/." "$SHARED/claude-code/"
    fi
  done
fi
mkdir -p "$STAGED"
if [[ -n "$SOURCE_DIR" ]]; then
  [[ -f "$SOURCE_DIR/install.sh" ]] || { printf 'ERROR: Source checkout lacks install.sh: %s\n' "$SOURCE_DIR" >&2; exit 1; }
  SOURCE_DIR="$(cd -- "$SOURCE_DIR" && pwd -P)"
  tar -C "$SOURCE_DIR" --exclude='./.git' --exclude='./.git/*' --exclude='*/.git' \
      --exclude='__pycache__' --exclude='*.pyc' -cf - . | tar -C "$STAGED" -xf -
  SOURCE_COMMIT="$(git -C "$SOURCE_DIR" rev-parse HEAD 2>/dev/null || printf 'unknown')"
else
  SOURCE_COMMIT="$(curl -fsSL https://api.github.com/repos/FlossWare/claude-ensemble/commits/main | python3 -c 'import json,sys; print(json.load(sys.stdin)["sha"])')"
  curl -fsSL "https://github.com/FlossWare/claude-ensemble/archive/$SOURCE_COMMIT.tar.gz" \
    | tar -xz -C "$STAGED" --strip-components=1
fi
# The development checkout has a convenience symlink to a Red Hat-owned
# claude-global-skills checkout. It is host-specific and must never ship in a
# standalone release (including archive-based installs). Keep other symlinks
# intact so structural validation can reject unexpected external links.
if [[ -L "$STAGED/claude-global-skills" ]]; then
  rm -- "$STAGED/claude-global-skills"
fi
[[ -f "$STAGED/install.sh" && ! -e "$STAGED/.git" && ! -L "$STAGED/.git" ]] || {
  printf 'ERROR: Source archive is incomplete or contains Git metadata.\n' >&2
  exit 1
}
# Mutable profile state is shared between releases. Never overwrite existing
# profile files with templates from a newer release.
rm -rf -- "$STAGED/claude-code"
ln -s ../../shared/claude-code "$STAGED/claude-code"
mkdir -p "$RELEASES"
mv -- "$STAGED" "$RELEASE"

if ! ENSEMBLE_INSTALL_ROOT="$ROOT" bash "$RELEASE/tools/ce-deployment" validate "$VERSION"; then
  rm -rf -- "$RELEASE"
  printf 'ERROR: Staged release failed structural validation.\n' >&2
  exit 1
fi

MANIFEST_TMP="$WORK/deployment.json"
python3 - "$MANIFEST_TMP" "$VERSION" "$SOURCE_COMMIT" <<'PY'
import datetime
import json
import sys
path, version, commit = sys.argv[1:]
with open(path, "w", encoding="utf-8") as output:
    json.dump({
        "schema_version": 1,
        "release": version,
        "source_commit": commit,
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "validation": "staged",
    }, output, indent=2)
    output.write("\n")
PY
if [[ "$STAGING_ONLY" == "1" ]]; then
  mv -f -- "$MANIFEST_TMP" "$ROOT/deployment.json"
  printf 'Staged Claude Ensemble %s at %s/releases/%s\n' "$VERSION" "$ROOT" "$VERSION"
  printf 'Source commit: %s\n' "$SOURCE_COMMIT"
  printf 'Validation: structural only; no service, systemd unit, current symlink, or Claude profile was changed.\n'
  printf 'To activate later, inspect the release and explicitly run: ENSEMBLE_INSTALL_ROOT=%q bash %q activate %q\n' "$ROOT" "$RELEASE/tools/ce-deployment" "$VERSION"
  exit 0
fi
if ! bash "$RELEASE/tools/ce-deployment" activate "$VERSION"; then
  printf 'ERROR: Activation failed. The release was retained for diagnosis and retry.\n' >&2
  exit 1
fi
python3 - "$MANIFEST_TMP" <<'PY'
import json, sys
path = sys.argv[1]
with open(path, encoding="utf-8") as source:
    manifest = json.load(source)
manifest["validation"] = "activated"
with open(path, "w", encoding="utf-8") as target:
    json.dump(manifest, target, indent=2)
    target.write("\n")
PY
mv -f -- "$MANIFEST_TMP" "$ROOT/deployment.json"
printf 'Installed Claude Ensemble %s at %s/current\n' "$VERSION" "$ROOT"
printf 'Source commit: %s\n' "$SOURCE_COMMIT"
printf 'Mutable Claude Code profile: %s/claude-code\n' "$SHARED"
printf 'Previous release retained for rollback; use tools/ce-deployment status to inspect releases.\n'
