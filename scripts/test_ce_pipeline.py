"""Isolated integration proof for the real Learning-to-Thompson outcome path.

All persistent state and Unix sockets live under a temporary directory. The
test does not touch the user's production Memory, Learning, or Thompson state.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


class LearningThompsonPipelineTests(unittest.TestCase):
    def test_real_learning_request_updates_isolated_thompson_service(self):
        with tempfile.TemporaryDirectory(prefix="ce-pipeline-test-") as tmp:
            root = Path(tmp)
            env = os.environ.copy()
            env.update({
                "ENSEMBLE_LOG_DIR": str(root / "logs"),
                "ENSEMBLE_LEARNING_DIR": str(root / "learning"),
                "ENSEMBLE_RUNTIME_DIR": str(root / "runtime"),
                "ENSEMBLE_THOMPSON_SOCKET": str(root / "thompson.sock"),
                "ENSEMBLE_THOMPSON_STATE_FILE": str(root / "thompson-state.json"),
            })
            script = r'''
import json
import socket
import sys
import time
import threading
from pathlib import Path

repo = Path(sys.argv[1])
tmp = Path(sys.argv[2])
sys.path.insert(0, str(repo))
sys.path.insert(0, str(repo / "learning-service"))
sys.path.insert(0, str(repo / "thompson-service"))

from learning_service import LearningService
from thompson_service import ThompsonService
from shared.thompson_client import ThompsonClient

class IsolatedMemory:
    def __init__(self):
        self.events = []
    def write_event(self, **event):
        self.events.append(event)
        return True

socket_path = tmp / "thompson.sock"
state_path = tmp / "thompson-state.json"
thompson = ThompsonService(socket_path, state_path)
thread = threading.Thread(target=thompson.start, daemon=True)
thread.start()
deadline = time.monotonic() + 3
while not socket_path.exists() and time.monotonic() < deadline:
    time.sleep(0.01)
assert socket_path.exists(), "isolated Thompson socket did not start"

memory = IsolatedMemory()
learning = LearningService(tmp / "learning.sock", tmp / "learning", memory)
learning.thompson_client = ThompsonClient(socket_path=socket_path, timeout=1)

task_id = "ce-diagnostic-isolated-pipeline-test"
request = {
    "op": "process_outcome",
    "task_id": task_id,
    "task_type": "diagnostic-integration",
    "model": "diagnostic-test-model",
    "rating": 5,
    "tokens": 7,
    "cost": 0.0,
}
try:
    client_end, service_end = socket.socketpair()
    client_end.settimeout(5)
    handler = threading.Thread(target=learning._handle_client, args=(service_end,), daemon=True)
    handler.start()
    client_end.sendall((json.dumps(request) + "\n").encode("utf-8"))
    response_bytes = bytearray()
    while b"\n" not in response_bytes and len(response_bytes) < 65536:
        chunk = client_end.recv(4096)
        if not chunk:
            break
        response_bytes.extend(chunk)
    client_end.close()
    handler.join(timeout=2)
    assert not handler.is_alive(), "Learning request handler did not finish"
    result = json.loads(bytes(response_bytes).split(b"\n", 1)[0].decode("utf-8"))
    assert result.get("ok") is True, result
    assert result.get("thompson") is True, result
    assert result.get("checkpoint_advanced") is True, result
    assert len(memory.events) >= 2, memory.events
    state = thompson.state.get_state()
    model = state["models"].get("diagnostic-test-model")
    assert model is not None, state
    assert model["calls"] == 1, model
    assert model["successes"] == 1, model
    assert learning.system.is_processed(task_id)
finally:
    thompson.stop()
    thread.join(timeout=2)
'''
            result = subprocess.run(
                [sys.executable, "-c", script, str(REPO_ROOT), str(root)],
                env=env,
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            self.assertEqual(
                0,
                result.returncode,
                msg=f"isolated pipeline failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
            )


if __name__ == "__main__":
    unittest.main()
