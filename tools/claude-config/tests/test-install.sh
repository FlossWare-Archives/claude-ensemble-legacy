#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
INIT_SCRIPT="$ROOT/../../scripts/ensemble-init.sh"
TOP_LEVEL_INSTALL="$ROOT/../../install.sh"
if grep -Eq '\${HOME}/Development/(github/)?FlossWare/claude-ensemble' "$TOP_LEVEL_INSTALL"; then
  echo "developer-specific default install path detected" >&2
  exit 1
fi
grep -Fq 'dirname "${BASH_SOURCE[0]}"' "$TOP_LEVEL_INSTALL"
if grep -Eq 'git pull|while true|load_credentials' "$INIT_SCRIPT" || grep -Fq 'disown $!' "$INIT_SCRIPT"; then
  echo "unsafe automatic shell initialization behavior detected" >&2
  exit 1
fi
if grep -Fq "'git', 'pull', 'origin', 'main'" "$INIT_SCRIPT"; then
  echo "automatic Git memory synchronization detected" >&2
  exit 1
fi
if grep -Fq '$HOME/.claude' "$INIT_SCRIPT" || grep -Fq "Path.home() / '.claude'" "$INIT_SCRIPT"; then
  echo "shell initialization must not default state into normal ~/.claude" >&2
  exit 1
fi
if grep -Fq 'source "$ENSEMBLE_ROOT/scripts/ensemble-init.sh" 2>/dev/null' "$INIT_SCRIPT"; then
  echo "recursive shell initialization detected" >&2
  exit 1
fi
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
export HOME="$TMP/home"
export FLOSSWARE_MEMORY_URL="http://127.0.0.1:9"
# Keep the disposable CE profile outside the default ~/.claude tree.
CLAUDE_HOME="$HOME/ce-profile"
export ENSEMBLE_CLAUDE_CODE_CONFIG_DIR="$CLAUDE_HOME"
mkdir -p "$CLAUDE_HOME"

cat > "$CLAUDE_HOME/settings.json" <<'JSON'
{
  "permissions": {"allow": ["Bash(git status:*)"]},
  "hooks": {
    "UserPromptSubmit": [
      {"hooks": [{"type": "command", "command": "/custom/existing-hook"}]}
    ]
  }
}
JSON

# Legacy repository hook symlink must be replaced without modifying its target.
rm -f "$CLAUDE_HOME/hooks/memory-search-on-prompt.js"
legacy_target="$TMP/legacy-claude-ensemble/hooks"
mkdir -p "$legacy_target" "$CLAUDE_HOME/hooks" "$CLAUDE_HOME/.flossware-claude-config"
cp "$ROOT/../../hooks/memory-search-on-prompt.js" "$legacy_target/memory-search-on-prompt.js"
ln -s "$legacy_target/memory-search-on-prompt.js" "$CLAUDE_HOME/hooks/memory-search-on-prompt.js"
legacy_before="$(sha256sum "$legacy_target/memory-search-on-prompt.js" | awk '{print $1}')"
python3 "$ROOT/lib/json_tool.py" manifest   "$CLAUDE_HOME/.flossware-claude-config/manifest.json"   "legacy"   "$CLAUDE_HOME/hooks/memory-search-on-prompt.js"   "$legacy_before"
bash "$ROOT/install.sh" --non-interactive
test -f "$CLAUDE_HOME/hooks/session-end-memory-capture.js"
# Context retrieval must run synchronously so Claude Code can consume its JSON
# additionalContext output; legacy async registrations are normalized by install.
python3 - "$CLAUDE_HOME/settings.json" "$CLAUDE_HOME/hooks/session-end-memory-capture.js" "$CLAUDE_HOME/hooks/memory-search-on-prompt.js" <<'PY'
import json,sys,shlex
settings=json.load(open(sys.argv[1],encoding="utf-8"))
session_command, prompt_command=sys.argv[2:]
registered=[h.get("command") for g in settings["hooks"]["SessionEnd"] for h in g.get("hooks",[])]
assert session_command in registered or shlex.quote(session_command) in registered, registered
prompt_hooks=[h for g in settings["hooks"]["UserPromptSubmit"] for h in g.get("hooks",[])
              if "memory-search-on-prompt.js" in h.get("command","")]
assert len(prompt_hooks)==1, prompt_hooks
assert prompt_hooks[0].get("async") is not True, prompt_hooks
assert int(prompt_hooks[0].get("timeout", 3)) >= 1, prompt_hooks
PY
test ! -L "$CLAUDE_HOME/hooks/memory-search-on-prompt.js"
test -f "$CLAUDE_HOME/hooks/memory-search-on-prompt.js"
test "$(sha256sum "$legacy_target/memory-search-on-prompt.js" | awk '{print $1}')" = "$legacy_before"

# Known deployed legacy prompt hooks are migrated by ownership hash.
legacy_user="$CLAUDE_HOME/hooks/user-prompt-submit.sh"
legacy_ingest="$CLAUDE_HOME/hooks/ingest-prompt"
cp "$ROOT/tests/fixtures/legacy-user-prompt-submit.sh" "$legacy_user"
cp "$ROOT/tests/fixtures/legacy-ingest-prompt" "$legacy_ingest"
python3 - "$CLAUDE_HOME/settings.json" "$legacy_user" "$legacy_ingest" <<'PY'
import json,sys,shlex
p,user,ingest=sys.argv[1:]
d=json.load(open(p,encoding="utf-8"))
d["hooks"]["UserPromptSubmit"]=[{"matcher":".*","hooks":[{"type":"command","command":shlex.quote(user)},{"type":"command","command":shlex.quote(ingest)}]}]
json.dump(d,open(p,"w",encoding="utf-8"),indent=2)
PY
bash "$ROOT/install.sh" --non-interactive
python3 - "$CLAUDE_HOME/settings.json" "$legacy_user" "$legacy_ingest" "$CLAUDE_HOME/hooks/memory-search-on-prompt.js" <<'PY'
import json,sys,os,shlex
p,user,ingest,canonical=sys.argv[1:]
d=json.load(open(p,encoding="utf-8"))
commands=[h.get("command","") for g in d["hooks"]["UserPromptSubmit"] for h in g.get("hooks",[])]
assert user not in commands and ingest not in commands, commands
assert canonical in commands or shlex.quote(canonical) in commands, commands
assert os.path.exists(user) and os.path.exists(ingest)
PY

# Exact known legacy SessionEnd scripts are unregistered without deleting their files.
legacy_session="$CLAUDE_HOME/hooks/session-end-comprehensive-capture.sh"
cp "$ROOT/../../hooks/session-end-comprehensive-capture.sh" "$legacy_session"
python3 - "$CLAUDE_HOME/settings.json" "$legacy_session" <<'PY'
import json,sys,shlex
p,legacy=sys.argv[1:]
d=json.load(open(p,encoding="utf-8"))
d.setdefault("hooks",{})["SessionEnd"]=[{"hooks":[{"type":"command","command":shlex.quote(legacy)}]}]
json.dump(d,open(p,"w",encoding="utf-8"),indent=2)
PY
bash "$ROOT/install.sh" --non-interactive
python3 - "$CLAUDE_HOME/settings.json" "$legacy_session" "$CLAUDE_HOME/hooks/session-end-memory-capture.js" <<'PY'
import json,sys,shlex,os
p,legacy,canonical=sys.argv[1:]
d=json.load(open(p,encoding="utf-8"))
commands=[h.get("command","") for g in d["hooks"]["SessionEnd"] for h in g.get("hooks",[])]
assert legacy not in commands and shlex.quote(legacy) not in commands, commands
assert canonical in commands or shlex.quote(canonical) in commands, commands
assert os.path.isfile(legacy), "migration must preserve the legacy script file"
PY

# A modified same-named script is not owned and must remain registered.
modified_user="$CLAUDE_HOME/hooks/user-prompt-submit.sh"
cp "$ROOT/tests/fixtures/modified-legacy-user-prompt-submit.sh" "$modified_user"
python3 - "$CLAUDE_HOME/settings.json" "$modified_user" <<'PY'
import json,sys,shlex
p,user=sys.argv[1:]
d=json.load(open(p,encoding="utf-8"))
d["hooks"]["UserPromptSubmit"]=[{"matcher":".*","hooks":[{"type":"command","command":shlex.quote(user)}]}]
json.dump(d,open(p,"w",encoding="utf-8"),indent=2)
PY
python3 "$ROOT/lib/json_tool.py" migrate-legacy "$CLAUDE_HOME/settings.json" >/dev/null
python3 - "$CLAUDE_HOME/settings.json" "$modified_user" <<'PY'
import json,sys,shlex
p,user=sys.argv[1:]
d=json.load(open(p,encoding="utf-8"))
commands=[h.get("command","") for g in d["hooks"]["UserPromptSubmit"] for h in g.get("hooks",[])]
assert user in commands or shlex.quote(user) in commands, commands
PY

# Existing managed hook spellings collapse to one entry without stealing
# an unrelated same-named hook or changing its matcher group.
legacy_settings_target="$TMP/legacy-settings/claude-ensemble/hooks"
mkdir -p "$legacy_settings_target"
cp "$ROOT/../../hooks/memory-search-on-prompt.js" "$legacy_settings_target/memory-search-on-prompt.js"
foreign_hook="$TMP/foreign/hooks/memory-search-on-prompt.js"
mkdir -p "$(dirname "$foreign_hook")"
printf '%s\n' '#!/bin/sh' 'echo unrelated' > "$foreign_hook"
python3 - "$CLAUDE_HOME/settings.json" "$legacy_settings_target/memory-search-on-prompt.js" "$foreign_hook" "$CLAUDE_HOME/hooks/memory-search-on-prompt.js" <<'PY'
import json,sys
p,legacy,foreign,managed_alias=sys.argv[1:]
d=json.load(open(p,encoding="utf-8"))
d["hooks"]["UserPromptSubmit"] = [
  {"matcher":"Remember|recall","hooks": [
    {"type":"command","command":legacy,"timeout":3,"async":True},
    {"type":"command","command":managed_alias,"timeout":3,"async":True}
  ]},
  {"matcher":"UserPromptSubmit","hooks": [
    {"type":"command","command":foreign,"timeout":3},
    {"type":"command","command":"/custom/existing-hook"}
  ]}
]
json.dump(d,open(p,"w",encoding="utf-8"),indent=2)
PY
bash "$ROOT/install.sh" --non-interactive
python3 - "$CLAUDE_HOME/settings.json" "$foreign_hook" <<'PY'
import json,sys,os
p,foreign=sys.argv[1:]
d=json.load(open(p,encoding="utf-8"))
groups=d["hooks"]["UserPromptSubmit"]
matches=[h for g in groups for h in g["hooks"] if h.get("command","").endswith("memory-search-on-prompt.js")]
assert len(matches)==2, matches
managed_path=os.path.join(os.path.dirname(p),"hooks","memory-search-on-prompt.js")
managed=[h for h in matches if h.get("command") == managed_path]
assert len(managed)==1, managed
foreign_group=next(g for g in groups if any(h.get("command")==foreign for h in g["hooks"]))
assert foreign_group["matcher"] == "UserPromptSubmit", foreign_group
assert any(h.get("command")=="/custom/existing-hook" for h in foreign_group["hooks"])
managed_group=next(g for g in groups if any(h is managed[0] for h in g["hooks"]))
assert managed_group["matcher"] == "Remember|recall", managed_group
assert managed[0].get("async") is not True, managed
assert int(managed[0].get("timeout", 3)) >= 1, managed
PY

bash "$ROOT/install.sh" --non-interactive
python3 - "$CLAUDE_HOME/settings.json" <<'PY'
import json,sys
d=json.load(open(sys.argv[1],encoding="utf-8"))
hooks=d["hooks"]["UserPromptSubmit"]
assert any(h.get("command")=="/custom/existing-hook" for g in hooks for h in g["hooks"])
assert any("memory-search-on-prompt.js" in h.get("command","") for g in hooks for h in g["hooks"])
assert d["permissions"]["allow"]
PY

bash "$ROOT/install.sh" --non-interactive
python3 - "$CLAUDE_HOME/settings.json" <<'PY'
import json,sys,os
d=json.load(open(sys.argv[1],encoding="utf-8"))
hooks=d["hooks"]["UserPromptSubmit"]
managed_path=os.path.join(os.path.dirname(sys.argv[1]),"hooks","memory-search-on-prompt.js")
matches=[h for g in hooks for h in g["hooks"] if h.get("command")==managed_path]
assert len(matches)==1, matches
PY

# Foreign hook conflict must refuse without --force.
rm -f "$CLAUDE_HOME/hooks/memory-search-on-prompt.js"
printf '%s\n' '#!/bin/sh' 'echo foreign' > "$CLAUDE_HOME/hooks/memory-search-on-prompt.js"
if bash "$ROOT/install.sh" --non-interactive; then
  echo "expected foreign hook conflict" >&2
  exit 1
fi

# --force replaces the foreign hook, with a backup created.
bash "$ROOT/install.sh" --non-interactive --force
grep -q "Canonical Claude Code context retrieval hook." "$CLAUDE_HOME/hooks/memory-search-on-prompt.js" || { echo "installed memory hook marker missing" >&2; exit 1; }
backup="$(find "$CLAUDE_HOME/.flossware-claude-config/backups" -name 'memory-search-on-prompt.js' -print -quit)"
test -n "$backup" || { echo "expected memory hook backup missing" >&2; exit 1; }

# Lock must prevent concurrent mutation.
mkdir "$CLAUDE_HOME/.flossware-claude-config/install.lock"
if bash "$ROOT/install.sh" --non-interactive; then
  echo "expected lock failure" >&2
  exit 1
fi
rmdir "$CLAUDE_HOME/.flossware-claude-config/install.lock"

# Uninstall must refuse a modified managed hook.
printf '%s\n' '// modified' >> "$CLAUDE_HOME/hooks/memory-search-on-prompt.js"
if bash "$ROOT/uninstall.sh"; then
  echo "expected modified-hook uninstall refusal" >&2
  exit 1
fi

# A clean uninstall succeeds.
cp "$ROOT/../../hooks/memory-search-on-prompt.js" "$CLAUDE_HOME/hooks/memory-search-on-prompt.js"
bash "$ROOT/install.sh" --non-interactive
bash "$ROOT/uninstall.sh"
test ! -e "$CLAUDE_HOME/hooks/memory-search-on-prompt.js"
test ! -e "$CLAUDE_HOME/hooks/session-end-memory-capture.js"
test ! -e "$CLAUDE_HOME/.flossware-claude-config/manifest.json"

# Rollback must restore an existing manifest instead of deleting it.
bash "$ROOT/install.sh" --non-interactive
manifest_before="$(cat "$CLAUDE_HOME/.flossware-claude-config/manifest.json")"
cp "$CLAUDE_HOME/settings.json" "$TMP/install-settings.before"
cp "$CLAUDE_HOME/hooks/memory-search-on-prompt.js" "$TMP/install-hook.before"
mkdir -p "$TMP/fakebin"
cat > "$TMP/fakebin/node" <<'SH'
#!/bin/sh
if [ "$2" = "$FLOSSWARE_EXPECTED_MEMORY_HOOK" ]; then
  : > "$FLOSSWARE_NODE_SOURCE_VALIDATED"
  exit 0
fi
if [ "$2" = "$FLOSSWARE_INSTALLED_MEMORY_HOOK" ]; then
  exit 1
fi
exit 1
SH
chmod +x "$TMP/fakebin/node"
export FLOSSWARE_EXPECTED_MEMORY_HOOK="$(cd "$ROOT/../../hooks" && pwd)/memory-search-on-prompt.js"
export FLOSSWARE_INSTALLED_MEMORY_HOOK="$CLAUDE_HOME/hooks/memory-search-on-prompt.js"
export FLOSSWARE_NODE_SOURCE_VALIDATED="$TMP/node-source-validated"
rm -f "$FLOSSWARE_NODE_SOURCE_VALIDATED"
if PATH="$TMP/fakebin:/usr/bin:/bin" bash "$ROOT/install.sh" --non-interactive; then
  echo "expected verification failure" >&2
  exit 1
fi
test -f "$FLOSSWARE_NODE_SOURCE_VALIDATED"
test -f "$CLAUDE_HOME/.flossware-claude-config/manifest.json"
test "$(cat "$CLAUDE_HOME/.flossware-claude-config/manifest.json")" = "$manifest_before"
cmp "$CLAUDE_HOME/settings.json" "$TMP/install-settings.before"
cmp "$CLAUDE_HOME/hooks/memory-search-on-prompt.js" "$TMP/install-hook.before"

# Uninstall must roll back hook, settings, and manifest if settings removal fails.
bash "$ROOT/install.sh" --non-interactive
cp "$CLAUDE_HOME/settings.json" "$TMP/uninstall-settings.before"
cp "$CLAUDE_HOME/hooks/memory-search-on-prompt.js" "$TMP/uninstall-hook.before"
cp "$CLAUDE_HOME/.flossware-claude-config/manifest.json" "$TMP/uninstall-manifest.before"
cat > "$TMP/fakebin/python3" <<'SH'
#!/bin/sh
if [ "$2" = "remove-hook" ]; then
  echo "injected remove-hook failure" >&2
  exit 1
fi
exec /usr/bin/python3 "$@"
SH
chmod +x "$TMP/fakebin/python3"
if PATH="$TMP/fakebin:$PATH" bash "$ROOT/uninstall.sh"; then
  echo "expected uninstall failure" >&2
  exit 1
fi
cmp "$CLAUDE_HOME/settings.json" "$TMP/uninstall-settings.before"
cmp "$CLAUDE_HOME/hooks/memory-search-on-prompt.js" "$TMP/uninstall-hook.before"
cmp "$CLAUDE_HOME/.flossware-claude-config/manifest.json" "$TMP/uninstall-manifest.before"
rm -f "$TMP/fakebin/python3"
# A modified managed hook must not be overwritten without --force.
bash "$ROOT/install.sh" --non-interactive
printf '%s\n' '// user modification' >> "$CLAUDE_HOME/hooks/memory-search-on-prompt.js"
if bash "$ROOT/install.sh" --non-interactive; then
  echo "expected modified managed hook conflict" >&2
  exit 1
fi
bash "$ROOT/install.sh" --non-interactive --force

# Uninstall must fail closed on malformed or incomplete ownership manifests.
manifest="$CLAUDE_HOME/.flossware-claude-config/manifest.json"
cp "$manifest" "$TMP/valid-manifest.json"
printf '%s\n' '{"product":"flossware-claude-config"}' > "$manifest"
if bash "$ROOT/uninstall.sh"; then
  echo "expected malformed manifest refusal" >&2
  exit 1
fi
cp "$TMP/valid-manifest.json" "$manifest"
python3 - "$manifest" <<'PY'
import json,sys
p=sys.argv[1]
d=json.load(open(p,encoding="utf-8"))
d["files"].pop(next(iter(d["files"])))
json.dump(d,open(p,"w",encoding="utf-8"))
PY
if bash "$ROOT/uninstall.sh"; then
  echo "expected missing manifest entry refusal" >&2
  exit 1
fi
cp "$TMP/valid-manifest.json" "$manifest"

# Hook commands must remain valid when HOME contains spaces.
rm -rf "$CLAUDE_HOME"
export HOME="$TMP/home with spaces"
CLAUDE_HOME="$TMP/ce profile with spaces"
export ENSEMBLE_CLAUDE_CODE_CONFIG_DIR="$CLAUDE_HOME"
mkdir -p "$CLAUDE_HOME"
bash "$ROOT/install.sh" --non-interactive
python3 - "$CLAUDE_HOME/settings.json" "$CLAUDE_HOME/hooks/memory-search-on-prompt.js" <<'PY'
import json,sys,shlex
settings=json.load(open(sys.argv[1],encoding="utf-8"))
command=next(h["command"] for g in settings["hooks"]["UserPromptSubmit"] for h in g["hooks"])
assert command == shlex.quote(sys.argv[2]), (command,sys.argv[2])
PY

# Standalone root hook must remain valid without a repository package.json.
node --check "$ROOT/../../hooks/memory-search-on-prompt.js"

python3 "$ROOT/tests/test-memory-sync.py"

# SessionEnd capture must always have an unconditional matcher-free registration.
session_settings="$TMP/session-end-settings.json"
session_hook="$CLAUDE_HOME/hooks/session-end-memory-capture.js"
session_source="$ROOT/../../hooks/session-end-memory-capture.js"
assert_session_coverage() {
  python3 "$ROOT/lib/json_tool.py" validate-settings "$1" "$CLAUDE_HOME/hooks/memory-search-on-prompt.js" "$session_hook" >/dev/null
  python3 - "$1" "$session_hook" <<'PY'
import json,sys,shlex
settings=json.load(open(sys.argv[1],encoding="utf-8"))
command=sys.argv[2]
groups=settings.get("hooks",{}).get("SessionEnd",[])
matches=[g for g in groups if any(h.get("type")=="command" and h.get("command") in (command,shlex.quote(command)) for h in g.get("hooks",[]) if isinstance(h,dict))]
assert len(matches)==1, matches
assert "matcher" not in matches[0], matches[0]
PY
}
# Existing canonical registration under a restricted matcher must be widened.
python3 - "$session_settings" "$session_hook" "$CLAUDE_HOME/hooks/memory-search-on-prompt.js" <<'PY'
import json,sys,shlex
p,command,prompt=sys.argv[1:]
json.dump({"hooks":{"UserPromptSubmit":[{"hooks":[{"type":"command","command":prompt}]}],"SessionEnd":[{"matcher":"clear","hooks":[{"type":"command","command":shlex.quote(command)}]}]}},open(p,"w",encoding="utf-8"))
PY
python3 "$ROOT/lib/json_tool.py" install-session-end-hook "$session_settings" "$session_hook" "$session_source"
assert_session_coverage "$session_settings"
# A restricted registration followed by an unconditional one must not cause
# deduplication to preserve the restricted group.
python3 - "$session_settings" "$session_hook" "$CLAUDE_HOME/hooks/memory-search-on-prompt.js" <<'PY'
import json,sys,shlex
p,command,prompt=sys.argv[1:]
quoted=shlex.quote(command)
json.dump({"hooks":{"UserPromptSubmit":[{"hooks":[{"type":"command","command":prompt}]}],"SessionEnd":[{"matcher":"clear","hooks":[{"type":"command","command":quoted}]},{"hooks":[{"type":"command","command":quoted}]}]}},open(p,"w",encoding="utf-8"))
PY
python3 "$ROOT/lib/json_tool.py" install-session-end-hook "$session_settings" "$session_hook" "$session_source"
assert_session_coverage "$session_settings"
# A mixed group keeps the unrelated handler and its matcher, while canonical
# capture moves to its own unconditional group.
python3 - "$session_settings" "$session_hook" "$CLAUDE_HOME/hooks/memory-search-on-prompt.js" <<'PY'
import json,sys,shlex
p,command,prompt=sys.argv[1:]
json.dump({"hooks":{"UserPromptSubmit":[{"hooks":[{"type":"command","command":prompt}]}],"SessionEnd":[{"matcher":"clear","hooks":[{"type":"command","command":shlex.quote(command)},{"type":"command","command":"/tmp/user-session-end-hook"}]}]}},open(p,"w",encoding="utf-8"))
PY
python3 "$ROOT/lib/json_tool.py" install-session-end-hook "$session_settings" "$session_hook" "$session_source"
assert_session_coverage "$session_settings"
python3 - "$session_settings" <<'PY'
import json,sys
settings=json.load(open(sys.argv[1],encoding="utf-8"))
group=next(g for g in settings["hooks"]["SessionEnd"] if any(h.get("command")=="/tmp/user-session-end-hook" for h in g.get("hooks",[]) if isinstance(h,dict)))
assert group.get("matcher")=="clear", group
PY


# Explicit Claude profile selection must isolate install from the real default HOME/.claude.
default_profile="$HOME/.claude"
mkdir -p "$default_profile"
printf '%s\n' '{"sentinel":"work-managed default profile"}' > "$default_profile/settings.json"
default_settings="$default_profile/settings.json"
default_before="$(sha256sum "$default_settings" | awk '{print $1}')"
ce_profile="$TMP/dedicated CE profile"
mkdir -p "$ce_profile"
ENSEMBLE_CLAUDE_CODE_CONFIG_DIR="$ce_profile" CLAUDE_CONFIG_DIR="$ce_profile" bash "$ROOT/install.sh" --non-interactive
test -f "$ce_profile/settings.json"
test -f "$ce_profile/hooks/memory-search-on-prompt.js"
test -f "$ce_profile/hooks/session-end-memory-capture.js"
test -f "$ce_profile/.flossware-claude-config/manifest.json"
test "$(sha256sum "$default_settings" | awk '{print $1}')" = "$default_before"
test ! -e "$default_profile/hooks"

# A hooks-directory symlink must be rejected before any write can escape the profile.
unsafe_profile="$TMP/unsafe CE profile"
outside_hooks="$TMP/outside hooks"
mkdir -p "$unsafe_profile" "$outside_hooks"
printf '%s\n' '{"permissions":{}}' > "$unsafe_profile/settings.json"
ln -s "$outside_hooks" "$unsafe_profile/hooks"
unsafe_settings_before="$(sha256sum "$unsafe_profile/settings.json" | awk '{print $1}')"
if ENSEMBLE_CLAUDE_CODE_CONFIG_DIR="$unsafe_profile" CLAUDE_CONFIG_DIR="$unsafe_profile" bash "$ROOT/install.sh" --non-interactive; then
  echo "expected symlinked hooks-directory refusal" >&2
  exit 1
fi
test "$(sha256sum "$unsafe_profile/settings.json" | awk '{print $1}')" = "$unsafe_settings_before"
test ! -e "$outside_hooks/memory-search-on-prompt.js"
test ! -e "$outside_hooks/session-end-memory-capture.js"

# Installer must fail closed without an explicitly selected CE profile.
no_profile_home="$TMP/no-profile-home"
mkdir -p "$no_profile_home/.claude"
printf '%s\n' '{"sentinel":"must remain untouched"}' > "$no_profile_home/.claude/settings.json"
if env -u ENSEMBLE_CLAUDE_CODE_CONFIG_DIR -u CLAUDE_CONFIG_DIR HOME="$no_profile_home" bash "$ROOT/install.sh" --non-interactive; then
  echo "expected install without an explicit profile to fail" >&2
  exit 1
fi
test ! -e "$no_profile_home/.claude/hooks"
grep -q 'must remain untouched' "$no_profile_home/.claude/settings.json"

# Installer must refuse a symlinked hooks directory and leave its target intact.
linked_profile="$TMP/linked-profile"
external_hooks="$TMP/external-hooks"
mkdir -p "$linked_profile" "$external_hooks"
printf '%s\n' '{"sentinel":"external hook target"}' > "$external_hooks/memory-search-on-prompt.js"
ln -s "$external_hooks" "$linked_profile/hooks"
if ENSEMBLE_CLAUDE_CODE_CONFIG_DIR="$linked_profile" HOME="$TMP/linked-home" bash "$ROOT/install.sh" --non-interactive --force; then
  echo "expected symlinked hooks directory to be refused" >&2
  exit 1
fi
grep -q 'external hook target' "$external_hooks/memory-search-on-prompt.js"
test ! -e "$linked_profile/settings.json"

echo "claude-config tests passed"
