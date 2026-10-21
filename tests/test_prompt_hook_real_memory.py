"""Local-only retrieval integration: real Memory REST and an installed hook copy.

No production profile, external service, model, or inference dependency is used.
Run: python -m pytest -q tests/test_prompt_hook_real_memory.py
"""
from __future__ import annotations

import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import urllib.request

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def retrieval_stack(tmp_path, monkeypatch):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required to execute the actual prompt hook")
    monkeypatch.setenv("ENSEMBLE_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("ENSEMBLE_MEMORY_DIR", str(tmp_path / "memory"))
    monkeypatch.setenv("ENSEMBLE_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.syspath_prepend(str(ROOT))
    monkeypatch.syspath_prepend(str(ROOT / "memory-service"))
    module = importlib.import_module("memory_service")
    service = module.MemoryService(tmp_path / "memory.sock", tmp_path / "memory")
    historical = "The orbital lighthouse deployment uses a copper release marker."
    knowledge = "The orbital lighthouse architecture requires a cobalt approval gate."
    assert service.store.write_file("historical-deployment", historical)
    source = tmp_path / "project" / "CLAUDE.md"
    source.parent.mkdir()
    source.write_text(knowledge, encoding="utf-8")
    service.store.ingest_claude_markdown(
        str(source), knowledge, hashlib.sha256(knowledge.encode()).hexdigest(),
        {"scope": str(source.parent)},
    )
    server = module.create_http_server(service, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    profile = tmp_path / "dedicated acceptance profile"
    hook = profile / "hooks" / "memory-search-on-prompt.js"
    hook.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / "hooks" / hook.name, hook)
    config_dir = profile / ".flossware-claude-config"
    config_dir.mkdir()
    (config_dir / "rag.json").write_text(json.dumps({
        "knowledge": {"max_queries": 1},
        "graph": {"enabled": False},
    }), encoding="utf-8")
    env = os.environ.copy()
    for key in ("CLAUDE_PROMPT", "FLOSSWARE_MEMORY_TIMEOUT_MS",
                "FLOSSWARE_GRAPH_TIMEOUT_MS", "FLOSSWARE_CE_RAG_CONFIG"):
        env.pop(key, None)
    env.update({
        "FLOSSWARE_MEMORY_URL": f"http://127.0.0.1:{server.server_port}",
        "FLOSSWARE_CE_HOOK_DIAGNOSTICS": "1",
        "ENSEMBLE_CLAUDE_CODE_CONFIG_DIR": str(profile),
        "CLAUDE_CONFIG_DIR": str(profile),
    })
    try:
        yield node, hook, env, historical, knowledge
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        assert not thread.is_alive(), "temporary Memory HTTP server failed to stop"


def invoke_hook(stack, prompt):
    node, hook, env, _, _ = stack
    result = subprocess.run(
        [node, str(hook)],
        input=json.dumps({
            "hook_event_name": "UserPromptSubmit",
            "session_id": "isolated-real-memory-regression",
            "prompt": prompt,
        }),
        text=True, capture_output=True, env=env, cwd=hook.parent,
        timeout=10, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "unavailable:" not in result.stderr, result.stderr
    return result


def test_semantic_rest_and_copied_hook_retrieve_actual_historical_content(retrieval_stack):
    _, hook, env, historical, knowledge = retrieval_stack
    request = urllib.request.Request(
        env["FLOSSWARE_MEMORY_URL"] + "/memory/search-semantic",
        data=json.dumps({"query": "orbital lighthouse", "top_k": 2,
                         "source": "memory"}).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(request, timeout=3) as response:
        direct = json.load(response)
    assert direct["ok"] is True
    assert any(historical in item.get("content", "") for item in direct["results"])
    assert hook.read_bytes() == (ROOT / "hooks" / hook.name).read_bytes()
    result = invoke_hook(retrieval_stack, "Explain the orbital lighthouse deployment and architecture.")
    assert result.stdout.strip(), f"Hook emitted no historical context. stderr={result.stderr!r}"
    output = json.loads(result.stdout)["hookSpecificOutput"]
    assert output["hookEventName"] == "UserPromptSubmit"
    assert historical in output["additionalContext"]
    assert knowledge in output["additionalContext"]
    assert output["contextRetrieval"]["memoryCount"] >= 1
    assert output["contextRetrieval"]["knowledgeCount"] >= 1


def test_no_matches_is_distinguished_from_failed_or_malformed_hook(retrieval_stack):
    result = invoke_hook(retrieval_stack, "zzquuxnovelidentifier998877")
    assert result.stdout == ""
    prefix = "CE_HOOK_DIAGNOSTICS "
    events = [json.loads(line[len(prefix):]) for line in result.stderr.splitlines()
              if line.startswith(prefix)]
    completed = [event for event in events if event.get("event") == "completed"]
    assert completed, f"No completion diagnostic: {result.stderr!r}"
    assert completed[-1]["memoryStatus"] == "ok"
    assert completed[-1]["knowledgeStatus"] == "empty"
    assert completed[-1]["contextEmitted"] is False
