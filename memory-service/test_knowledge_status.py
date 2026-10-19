from __future__ import annotations

import hashlib
import json
import sys
import threading
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SERVICE_DIR))

from memory_service import MemoryService, create_http_server  # noqa: E402


def test_knowledge_status_reports_only_active_indexed_document_count(tmp_path: Path) -> None:
    service = MemoryService(tmp_path / "runtime" / "memory.sock", tmp_path / "memory")
    server = create_http_server(service, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]

        def status() -> dict:
            request = urllib.request.Request(
                f"http://127.0.0.1:{port}/memory/knowledge/status",
                data=b"{}",
                headers={"content-type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=2) as response:
                return json.loads(response.read().decode("utf-8"))

        empty = status()
        assert empty == {
            "ok": True,
            "source": "claude-code",
            "status": "empty",
            "indexed_document_count": 0,
        }

        content = "# Lifecycle\nSessionEnd captures metadata only."
        service.store.ingest_claude_markdown(
            "/home/test/CLAUDE.md",
            content,
            hashlib.sha256(content.encode("utf-8")).hexdigest(),
            {"scope": "/home/test"},
        )
        available = status()
        assert available == {
            "ok": True,
            "source": "claude-code",
            "status": "available",
            "indexed_document_count": 1,
        }
        assert "/home/test/CLAUDE.md" not in json.dumps(available)
        assert content not in json.dumps(available)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
