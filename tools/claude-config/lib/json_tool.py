#!/usr/bin/env python3
from pathlib import Path
import json,os,sys,shlex,re

def load(path):
    p=Path(path)
    if not p.exists(): return {}
    with p.open(encoding="utf-8") as f: value=json.load(f)
    if not isinstance(value,dict): raise ValueError(path+" must contain a JSON object")
    return value

def save(path,value):
    p=Path(path); p.parent.mkdir(parents=True,exist_ok=True)
    tmp=p.with_name(p.name+".tmp")
    with tmp.open("w",encoding="utf-8") as f: json.dump(value,f,indent=2,sort_keys=True); f.write("\n")
    os.replace(tmp,p)

PRODUCT="flossware-claude-config"
SHA256_RE=re.compile(r"^[0-9a-f]{64}$")
LEGACY_HOOK_SHAS={
    "hooks/user-prompt-submit.sh": {
        "d424ad860680d9f3b07455eb23c5a3304bbab78280088dc2902f3f866417a458",
    },
    "hooks/ingest-prompt": {
        "bd8443ec9be3542c24c3a93d637c3a2b0770ff8494d7e0857ea00f8d7c015839",
    },
    "hooks/ingest-session-end": {
        "94b6e0a735c67076b2a1ed05fbd45556f00d7ac8889c1d98f311cb0f56d67f06",
    },
    "hooks/session-end-comprehensive-capture.sh": {
        "41192633ca004f186e4b3fa31782dbbbb9badd39e0cf827a0e07db5053daa32c",
        "3be8e65fffc19ed75a9076ece986ed80dafbd39f4a41cba1bd0a18d780cdd7e5",
    },
    "hooks/session-end-learning-capture.sh": {
        "22b65ebe3a726376fe7b510930554385380f64f03b12c55a88af043726736b15",
    },
}

def entry(command): return {"hooks":[{"type":"command","command":shlex.quote(command),"timeout":3}]}

def command_path(command):
    if not isinstance(command,str): return None
    try:
        parts=shlex.split(command)
    except ValueError:
        return None
    if len(parts) != 1: return None
    return Path(parts[0]).expanduser()

def is_memory_hook_command(command, managed_command, source_sha):
    path=command_path(command)
    managed_path=command_path(managed_command)
    if path is None or managed_path is None or path.name != "memory-search-on-prompt.js":
        return False
    if path == managed_path:
        return True
    try:
        return path.is_file() and sha256_file(path) == source_sha
    except OSError:
        return False

def is_owned_legacy_hook(command):
    path=command_path(command)
    if path is None or not path.is_file(): return False
    for name, expected_sha in LEGACY_HOOK_SHAS.items():
        if path.name == Path(name).name:
            try:
                return sha256_file(path) in expected_sha
            except OSError:
                return False
    return False


def migrate_legacy_hooks(path):
    data=validate_input(path); hooks=data.get("hooks",{})
    removed=[]
    for event_name in ("UserPromptSubmit", "SessionEnd"):
        cleaned=[]
        for group in hooks.get(event_name,[]):
            if not isinstance(group,dict): cleaned.append(group); continue
            handlers=[]
            for h in group.get("hooks",[]):
                if isinstance(h,dict) and h.get("type")=="command" and is_owned_legacy_hook(h.get("command")):
                    removed.append(command_path(h.get("command")))
                    continue
                handlers.append(h)
            if handlers:
                updated=dict(group); updated["hooks"]=handlers; cleaned.append(updated)
        hooks[event_name]=cleaned
    save(path,data)
    for legacy_path in removed: print(str(legacy_path))

def sha256_file(path):
    import hashlib
    digest=hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def validate_input(path):
    data=load(path); hooks=data.get("hooks",{})
    if not isinstance(hooks,dict): raise ValueError("settings hooks must be an object")
    for event_name in ("UserPromptSubmit", "SessionEnd"):
        event=hooks.get(event_name,[])
        if not isinstance(event,list): raise ValueError(event_name+" must be an array")
        for group in event:
            if isinstance(group,dict) and not isinstance(group.get("hooks",[]),list): raise ValueError("hook group hooks must be an array")
    return data

def manifest_data(path,hook):
    data=load(path)
    if data.get("product") != PRODUCT: raise ValueError("manifest product mismatch")
    files=data.get("files")
    if not isinstance(files,dict): raise ValueError("manifest files missing")
    item=files.get(hook)
    if not isinstance(item,dict) or item.get("managed") is not True: raise ValueError("managed hook entry missing")
    sha=item.get("sha256")
    if not isinstance(sha,str) or not SHA256_RE.fullmatch(sha): raise ValueError("managed hook checksum missing or invalid")
    return item

def validate_manifest(path,hook): manifest_data(path,hook); print("✓ manifest ownership is valid")
def manifest_sha(path,hook): print(manifest_data(path,hook)["sha256"])

def install_hook(path,command,source):
    data=validate_input(path); hooks=data.setdefault("hooks",{})
    if not isinstance(hooks,dict): raise ValueError("settings hooks must be an object")
    event=hooks.setdefault("UserPromptSubmit",[])
    if not isinstance(event,list): raise ValueError("UserPromptSubmit must be an array")
    source_path=Path(source)
    source_sha=sha256_file(source_path)

    managed=False
    cleaned=[]
    for group in event:
        if not isinstance(group,dict):
            cleaned.append(group)
            continue
        handlers=[]
        group_managed=False
        for h in group.get("hooks",[]):
            if isinstance(h,dict) and h.get("type")=="command" and is_memory_hook_command(h.get("command"), command, source_sha):
                if not managed:
                    replacement=dict(h)
                    replacement["command"]=shlex.quote(command)
                    # UserPromptSubmit must finish synchronously to return
                    # hookSpecificOutput.additionalContext to Claude Code.
                    # Preserve other handler settings, but repair legacy async
                    # registrations when normalizing this CE-owned hook.
                    replacement.pop("async", None)
                    replacement.setdefault("timeout", 3)
                    handlers.append(replacement)
                    managed=True
                    group_managed=True
                continue
            handlers.append(h)
        if handlers:
            updated=dict(group)
            updated["hooks"]=handlers
            cleaned.append(updated)
        elif group_managed:
            cleaned.append({"hooks":[replacement]})

    if not managed:
        cleaned.append(entry(command))
    hooks["UserPromptSubmit"]=cleaned
    save(path,data)

def remove_hook(path,command):
    if not Path(path).exists(): return
    data=load(path); hooks=data.get("hooks",{})
    if not isinstance(hooks,dict): return
    event=hooks.get("UserPromptSubmit",[])
    if not isinstance(event,list): return
    cleaned=[]
    for group in event:
        if not isinstance(group,dict): cleaned.append(group); continue
        handlers=[h for h in group.get("hooks",[]) if not (isinstance(h,dict) and h.get("type")=="command" and h.get("command") in (command,shlex.quote(command)))]
        if handlers:
            group=dict(group); group["hooks"]=handlers; cleaned.append(group)
    hooks["UserPromptSubmit"]=cleaned; save(path,data)

def install_session_end_hook(path,command,source):
    data=validate_input(path); hooks=data.setdefault("hooks",{})
    event=hooks.setdefault("SessionEnd",[])
    source_sha=sha256_file(Path(source))
    cleaned=[]
    for group in event:
        if not isinstance(group,dict):
            cleaned.append(group); continue
        handlers=[]
        for h in group.get("hooks",[]):
            if isinstance(h,dict) and h.get("type")=="command":
                existing=command_path(h.get("command"))
                if existing and existing.name=="session-end-memory-capture.js":
                    owned=(existing==Path(command) or (existing.is_file() and sha256_file(existing)==source_sha))
                    if owned:
                        # Re-register below in a matcher-free group. Keeping the
                        # old group would inherit its exit-reason matcher and
                        # silently narrow SessionEnd coverage.
                        continue
            handlers.append(h)
        if handlers:
            updated=dict(group); updated["hooks"]=handlers; cleaned.append(updated)
    # Exactly one canonical registration is added without a matcher, while
    # unrelated handlers retain their original groups and matchers.
    cleaned.append(entry(command))
    hooks["SessionEnd"]=cleaned
    save(path,data)

def remove_session_end_hook(path,command):
    if not Path(path).exists(): return
    data=load(path); hooks=data.get("hooks",{})
    if not isinstance(hooks,dict): return
    event=hooks.get("SessionEnd",[])
    if not isinstance(event,list): return
    cleaned=[]
    for group in event:
        if not isinstance(group,dict): cleaned.append(group); continue
        handlers=[h for h in group.get("hooks",[]) if not (isinstance(h,dict) and h.get("type")=="command" and h.get("command") in (command,shlex.quote(command)))]
        if handlers:
            updated=dict(group); updated["hooks"]=handlers; cleaned.append(updated)
    hooks["SessionEnd"]=cleaned; save(path,data)

def main():
    op=sys.argv[1]
    if op=="install-hook": install_hook(sys.argv[2],sys.argv[3],sys.argv[4])
    elif op=="migrate-legacy": migrate_legacy_hooks(sys.argv[2])
    elif op=="remove-hook": remove_hook(sys.argv[2],sys.argv[3])
    elif op=="install-session-end-hook": install_session_end_hook(sys.argv[2],sys.argv[3],sys.argv[4])
    elif op=="remove-session-end-hook": remove_session_end_hook(sys.argv[2],sys.argv[3])
    elif op=="create-settings": save(sys.argv[2],{"hooks":{"UserPromptSubmit":[entry(sys.argv[3])],"SessionEnd":[entry(sys.argv[4])]}})
    elif op=="manifest":
        files={}
        for index in range(4,len(sys.argv),2):
            files[sys.argv[index]]={"managed":True,"sha256":sys.argv[index+1]}
        save(sys.argv[2],{"product":PRODUCT,"version":sys.argv[3],"files":files})
    elif op=="validate-input": validate_input(sys.argv[2])
    elif op=="validate-settings":
        data=validate_input(sys.argv[2]); command=shlex.quote(sys.argv[3])
        for event_name,hook_command in (("UserPromptSubmit",sys.argv[3]),("SessionEnd",sys.argv[4])):
            event=data.get("hooks",{}).get(event_name,[])
            quoted=shlex.quote(hook_command)
            matching_groups=[g for g in event if isinstance(g,dict) and any(isinstance(h,dict) and h.get("type")=="command" and h.get("command") in (quoted,hook_command) for h in g.get("hooks",[]))]
            if not matching_groups:
                raise ValueError("managed "+event_name+" hook is missing")
            if event_name=="SessionEnd" and not any("matcher" not in g for g in matching_groups):
                raise ValueError("managed SessionEnd hook lacks unconditional coverage")
        print("✓ settings contain the managed prompt and unconditional session-end hooks")
    elif op=="validate-manifest": validate_manifest(sys.argv[2],sys.argv[3])
    elif op=="manifest-sha": manifest_sha(sys.argv[2],sys.argv[3])
    elif op=="plan":
        print("Claude Configuration Plan")
        print("CREATE/KEEP "+sys.argv[3])
        print("MODIFY "+sys.argv[2]+" with UserPromptSubmit retrieval and SessionEnd capture hooks")
        print("BACKUP existing settings before mutation")
        print("VERIFY settings JSON, hook syntax, and Memory REST health")
    else: raise SystemExit("unknown operation: "+op)
if __name__=="__main__":
    main()
