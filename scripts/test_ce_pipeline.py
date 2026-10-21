"""Isolated integration proof for the real Learning-to-Thompson outcome path.

All persistent state and Unix sockets live under a temporary directory. The
test does not touch the user's production Memory, Learning, or Thompson state.
"""
from __future__ import annotations

import os
import socket
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
            def free_port():
                with socket.socket() as listener:
                    listener.bind(("127.0.0.1", 0))
                    return listener.getsockname()[1]
            learning_port = free_port()
            thompson_port = free_port()
            env.update({
                "ENSEMBLE_LOG_DIR": str(root / "logs"),
                "ENSEMBLE_LEARNING_HTTP_PORT": str(learning_port),
                "ENSEMBLE_THOMPSON_HTTP_PORT": str(thompson_port),
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
import urllib.request
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

socket_path = tmp / "unused-thompson.sock"
state_path = tmp / "thompson-state.json"
thompson = ThompsonService(socket_path, state_path)
thompson_thread = threading.Thread(target=thompson.start, daemon=True)
thompson_thread.start()
thompson_url = "http://127.0.0.1:" + os.environ["ENSEMBLE_THOMPSON_HTTP_PORT"]
deadline = time.monotonic() + 3
while time.monotonic() < deadline:
    try:
        with urllib.request.urlopen(thompson_url + "/health", timeout=0.2):
            break
    except Exception:
        time.sleep(0.01)
else:
    raise AssertionError("isolated Thompson REST endpoint did not start")

memory = IsolatedMemory()
learning = LearningService(tmp / "unused-learning.sock", tmp / "learning", memory)
learning.thompson_client = ThompsonClient(service_url=thompson_url, timeout=1)
learning_thread = threading.Thread(target=learning.start, daemon=True)
learning_thread.start()
learning_url = "http://127.0.0.1:" + os.environ["ENSEMBLE_LEARNING_HTTP_PORT"]
deadline = time.monotonic() + 3
while time.monotonic() < deadline:
    try:
        with urllib.request.urlopen(learning_url + "/health", timeout=0.2):
            break
    except Exception:
        time.sleep(0.01)
else:
    raise AssertionError("isolated Learning REST endpoint did not start")

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
    body = json.dumps(request).encode("utf-8")
    http_request = urllib.request.Request(
        learning_url + "/learning", data=body, method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    with urllib.request.urlopen(http_request, timeout=5) as response:
        result = json.loads(response.read().decode("utf-8"))
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
    learning.stop()
    thompson.stop()
    learning_thread.join(timeout=2)
    thompson_thread.join(timeout=2)
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
