#!/usr/bin/env python3
"""Manage per-profile Claude Ensemble RAG hook behavior."""
import json, os, sys, tempfile
from pathlib import Path

DEFAULTS = {
    "memory": {"enabled": True, "timeout_ms": 1500, "max_results": 10},
    "knowledge": {"enabled": True, "timeout_ms": 1500, "max_results": 10, "max_queries": 3},
    "graph": {"enabled": True, "timeout_ms": 400, "max_roots": 3, "max_nodes": 8, "max_context_chars": 3000},
}
RANGES = {
    "memory.timeout_ms": (50, 10000), "memory.max_results": (1, 10),
    "knowledge.timeout_ms": (50, 10000), "knowledge.max_results": (1, 10), "knowledge.max_queries": (1, 3),
    "graph.timeout_ms": (50, 5000), "graph.max_roots": (1, 3), "graph.max_nodes": (1, 8),
    "graph.max_context_chars": (100, 3000),
}

def selected_config_path():
    selected = os.environ.get("ENSEMBLE_CLAUDE_CODE_CONFIG_DIR") or os.environ.get("CLAUDE_CONFIG_DIR")
    if not selected:
        raise ValueError("select a dedicated profile with ENSEMBLE_CLAUDE_CODE_CONFIG_DIR (or CLAUDE_CONFIG_DIR); refusing to use ~/.claude")
    profile = Path(selected).expanduser()
    if profile.is_symlink():
        raise ValueError("refusing to use a symlinked Claude profile directory")
    home_profile = (Path.home() / ".claude").resolve()
    resolved = profile.resolve()
    if resolved == home_profile or home_profile in resolved.parents:
        raise ValueError("selected profile resolves inside ~/.claude; refusing to modify the separately managed profile")
    state = profile / ".flossware-claude-config"
    if state.is_symlink():
        raise ValueError("refusing to write through a symlinked profile state directory")
    return state / "rag.json"

def validate(value):
    if not isinstance(value, dict) or set(value) - set(DEFAULTS):
        raise ValueError("configuration must contain only memory, knowledge, and graph objects")
    result = json.loads(json.dumps(DEFAULTS))
    for section, settings in value.items():
        if not isinstance(settings, dict) or set(settings) - set(result[section]):
            raise ValueError("invalid section or setting: " + section)
        result[section].update(settings)
    for section, settings in result.items():
        if not isinstance(settings["enabled"], bool):
            raise ValueError(section + ".enabled must be true or false")
        for key, item in settings.items():
            if key == "enabled":
                continue
            low, high = RANGES[section + "." + key]
            if isinstance(item, bool) or not isinstance(item, int) or not low <= item <= high:
                raise ValueError(section + "." + key + " must be an integer from " + str(low) + " to " + str(high))
    return result

def read_config(path):
    if not path.exists():
        return validate({})
    try:
        return validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("cannot read valid configuration at " + str(path) + ": " + str(exc)) from exc

def write_config(path, config):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    fd, temporary = tempfile.mkstemp(prefix=".rag.json.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(config, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

def main(argv):
    try:
        path = selected_config_path()
        command = argv[0] if argv else "show"
        if command == "show" and len(argv) == 1:
            print(json.dumps(read_config(path), indent=2, sort_keys=True))
            return 0
        if command == "reset" and len(argv) == 1:
            write_config(path, validate({}))
            print("Reset RAG configuration: " + str(path))
            return 0
        if command == "set" and len(argv) >= 2:
            config = read_config(path)
            for assignment in argv[1:]:
                if "=" not in assignment or "." not in assignment.split("=", 1)[0]:
                    raise ValueError("expected section.setting=value: " + assignment)
                key, raw = assignment.split("=", 1)
                section, setting = key.split(".", 1)
                if section not in config or setting not in config[section]:
                    raise ValueError("unknown setting: " + key)
                if setting == "enabled":
                    if raw not in ("true", "false"):
                        raise ValueError(key + " must be true or false")
                    value = raw == "true"
                else:
                    try:
                        value = int(raw)
                    except ValueError as exc:
                        raise ValueError(key + " must be an integer") from exc
                config[section][setting] = value
            write_config(path, validate(config))
            print("Updated RAG configuration: " + str(path))
            return 0
        print("Usage: claude-config rag-config [show | set section.setting=value [...] | reset]", file=sys.stderr)
        return 2
    except (OSError, ValueError) as exc:
        print("RAG config error: " + str(exc), file=sys.stderr)
        return 1

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
