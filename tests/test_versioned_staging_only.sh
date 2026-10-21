#!/usr/bin/env bash
# Verify staging-only installation cannot activate services or import live profiles.
set -Eeuo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/ce-staging-test.XXXXXX")"
trap 'rm -rf -- "$WORK"' EXIT

export HOME="$WORK/home"
mkdir -p "$HOME" "$HOME/.claude" "$WORK/mock-bin"
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
cp "$REPO_ROOT/tools/managed_unit_config.py" "$SOURCE/tools/managed_unit_config.py"
for service_dir in memory-service thompson-service learning-service graph-service server; do
  cp -a "$REPO_ROOT/$service_dir" "$SOURCE/"
done
# Model the developer-only absolute symlink present in the real repository.
mkdir -p "$WORK/external-rh-skills"
ln -s "$WORK/external-rh-skills" "$SOURCE/claude-global-skills"
git -C "$SOURCE" init -q
git -C "$SOURCE" config user.name "CE staging test"
git -C "$SOURCE" config user.email "ce-staging-test@example.invalid"
git -C "$SOURCE" add install.sh tools/ce-deployment tools/managed_unit_config.py memory-service thompson-service learning-service graph-service server
git -C "$SOURCE" -c user.name="CE staging test" -c user.email="ce-staging-test@example.invalid" commit -qm "fixture"

# Exercise the real-host layout where a .FlossWare symlink resolves under Development.
# A symlinked ~/.FlossWare must be rejected rather than silently staging into
# a development checkout or another redirected location.
mkdir -p "$WORK/redirected"
ln -s "$WORK/redirected" "$HOME/.FlossWare"
if ENSEMBLE_INSTALL_ROOT="$HOME/.FlossWare/staging-target" ENSEMBLE_STAGING_ONLY=1 \
    bash "$REPO_ROOT/tools/install-ce-versioned.sh" 0.1 "$SOURCE" >"$WORK/symlink.out" 2>&1; then
  echo "FAIL: installer accepted a symlinked ~/.FlossWare" >&2
  exit 1
fi
grep -q 'must be a real directory, not a symlink' "$WORK/symlink.out" || {
  cat "$WORK/symlink.out" >&2
  echo "FAIL: symlinked ~/.FlossWare refused for an unexpected reason" >&2
  exit 1
}
rm "$HOME/.FlossWare"
mkdir -p "$HOME/.FlossWare/claude-ensemble-profile"
printf 'legacy-profile-sentinel\n' > "$HOME/.FlossWare/claude-ensemble-profile/sentinel"

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

# A checkout whose HEAD references a missing Git object must fail closed. It must
# not produce a staged manifest containing the placeholder "unknown" commit.
BAD_SOURCE="$WORK/bad-source"
cp -a "$SOURCE" "$BAD_SOURCE"
BAD_HEAD_REF="$(git -C "$BAD_SOURCE" symbolic-ref HEAD)"
printf '%s\n' '1111111111111111111111111111111111111111' > "$BAD_SOURCE/.git/$BAD_HEAD_REF"
BAD_ROOT="$HOME/.FlossWare/bad-staging-target"
BAD_SOURCE_STATUS_FILE="$WORK/bad-source.status"
# Run the expected-failure case in its own wrapper. The wrapper always exits
# successfully after recording the installer status, so the parent test's
# errexit behavior cannot accidentally abort before assertions run.
cat > "$WORK/run-bad-source.sh" <<'WRAPPER'
#!/usr/bin/env bash
set +e
ENSEMBLE_INSTALL_ROOT="$1" ENSEMBLE_STAGING_ONLY=1 \
  bash "$2/tools/install-ce-versioned.sh" 0.1 "$3" >"$4" 2>&1
status=$?
printf '%s\n' "$status" > "$5"
exit 0
WRAPPER
bash "$WORK/run-bad-source.sh" "$BAD_ROOT" "$REPO_ROOT" "$BAD_SOURCE" "$WORK/bad-source.out" "$BAD_SOURCE_STATUS_FILE"
[[ -s "$BAD_SOURCE_STATUS_FILE" ]] || {
  echo "FAIL: invalid-source wrapper did not record installer status" >&2
  exit 1
}
BAD_SOURCE_STATUS="$(cat "$BAD_SOURCE_STATUS_FILE")"
if [[ "$BAD_SOURCE_STATUS" -eq 0 ]]; then
  echo "FAIL: staging accepted a source checkout with an unreadable HEAD commit" >&2
  cat "$WORK/bad-source.out" >&2
  exit 1
fi
if [[ -e "$BAD_ROOT/deployment.json" || -e "$BAD_ROOT/releases/0.1" ]]; then
  echo "FAIL: staging created a release or manifest for an unverifiable source commit" >&2
  cat "$WORK/bad-source.out" >&2
  find "$BAD_ROOT" -mindepth 1 -maxdepth 3 -print >&2 || true
  exit 1
fi

echo "PASS: staging-only deployment is isolated; no activation, systemctl, or profile migration occurred."
