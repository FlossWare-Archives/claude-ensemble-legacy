#!/usr/bin/env bash
# Verify staging-only installation cannot activate services or import live profiles.
set -Eeuo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/ce-staging-test.XXXXXX")"
trap 'rm -rf -- "$WORK"' EXIT

export HOME="$WORK/home"
mkdir -p "$HOME/.FlossWare/claude-ensemble-profile" "$HOME/.claude" "$WORK/mock-bin"
printf 'legacy-profile-sentinel\n' > "$HOME/.FlossWare/claude-ensemble-profile/sentinel"
printf 'ordinary-profile-sentinel\n' > "$HOME/.claude/sentinel"
SYSTEMCTL_LOG="$WORK/systemctl.log"
export SYSTEMCTL_LOG
cat > "$WORK/mock-bin/systemctl" <<'MOCK'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$SYSTEMCTL_LOG"
exit 99
MOCK
chmod +x "$WORK/mock-bin/systemctl"
export PATH="$WORK/mock-bin:$PATH"

SOURCE="$WORK/source"
mkdir -p "$SOURCE/tools"
printf '#!/usr/bin/env bash\nexit 0\n' > "$SOURCE/install.sh"
cp "$REPO_ROOT/tools/ce-deployment" "$SOURCE/tools/ce-deployment"
# Model the developer-only absolute symlink present in the real repository.
mkdir -p "$WORK/external-rh-skills"
ln -s "$WORK/external-rh-skills" "$SOURCE/claude-global-skills"
git -C "$SOURCE" init -q
git -C "$SOURCE" config user.name "CE staging test"
git -C "$SOURCE" config user.email "ce-staging-test@example.invalid"
git -C "$SOURCE" add install.sh tools/ce-deployment
git -C "$SOURCE" -c user.name="CE staging test" -c user.email="ce-staging-test@example.invalid" commit -qm "fixture"

ROOT="$HOME/.FlossWare/staging-target"
ENSEMBLE_INSTALL_ROOT="$ROOT" ENSEMBLE_STAGING_ONLY=1 \
  bash "$REPO_ROOT/tools/install-ce-versioned.sh" 0.1 "$SOURCE"

[[ -d "$ROOT/releases/0.1" ]] || { echo "FAIL: release was not staged" >&2; exit 1; }
[[ ! -e "$ROOT/releases/0.1/claude-global-skills" && ! -L "$ROOT/releases/0.1/claude-global-skills" ]] || {
  echo "FAIL: developer-only external skills symlink leaked into release" >&2; exit 1;
}
[[ -f "$ROOT/deployment.json" ]] || { echo "FAIL: staged manifest is missing" >&2; exit 1; }
python3 - "$ROOT/deployment.json" "$SOURCE" <<'PY'
import json, subprocess, sys
manifest = json.load(open(sys.argv[1], encoding="utf-8"))
expected = subprocess.check_output(["git", "-C", sys.argv[2], "rev-parse", "HEAD"], text=True).strip()
assert manifest["validation"] == "staged", manifest
assert manifest["source_commit"] == expected, manifest
PY
[[ ! -e "$ROOT/current" && ! -L "$ROOT/current" ]] || { echo "FAIL: staging created current symlink" >&2; exit 1; }
[[ ! -e "$ROOT/claude-code" ]] || { echo "FAIL: staging created a legacy profile path" >&2; exit 1; }
[[ -z "$(find "$ROOT/shared/claude-code" -mindepth 1 -maxdepth 1 -print -quit)" ]] || {
  echo "FAIL: staging imported profile content" >&2; exit 1;
}
[[ "$(cat "$HOME/.FlossWare/claude-ensemble-profile/sentinel")" == "legacy-profile-sentinel" ]] || {
  echo "FAIL: legacy profile changed" >&2; exit 1;
}
[[ "$(cat "$HOME/.claude/sentinel")" == "ordinary-profile-sentinel" ]] || {
  echo "FAIL: ordinary Claude profile changed" >&2; exit 1;
}
[[ ! -e "$SYSTEMCTL_LOG" ]] || { echo "FAIL: systemctl was invoked in staging-only mode" >&2; exit 1; }

# A HOME subdirectory may resolve through a symlink into Development (as
# ~/.FlossWare does on some hosts). Internal release links must still validate.
mkdir -p "$HOME/Development/FlossWare"
DEV_ROOT="$HOME/Development/FlossWare/staging-target"
ENSEMBLE_INSTALL_ROOT="$DEV_ROOT" ENSEMBLE_STAGING_ONLY=1 \
  bash "$REPO_ROOT/tools/install-ce-versioned.sh" 0.1 "$SOURCE"
[[ -f "$DEV_ROOT/deployment.json" ]] || {
  echo "FAIL: staging failed when target resolved beneath HOME/Development" >&2; exit 1;
}

# Staging must fail closed instead of reusing a non-empty root.
if ENSEMBLE_INSTALL_ROOT="$ROOT" ENSEMBLE_STAGING_ONLY=1 \
    bash "$REPO_ROOT/tools/install-ce-versioned.sh" 0.2 "$SOURCE" >"$WORK/reuse.out" 2>&1; then
  echo "FAIL: staging accepted a non-empty target root" >&2
  exit 1
fi
grep -q 'requires a new or empty install root' "$WORK/reuse.out" || {
  cat "$WORK/reuse.out" >&2
  echo "FAIL: staging refused root for an unexpected reason" >&2
  exit 1
}

echo "PASS: staging-only deployment is isolated; no activation, systemctl, or profile migration occurred."
