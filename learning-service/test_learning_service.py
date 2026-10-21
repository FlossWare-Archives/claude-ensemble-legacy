#!/usr/bin/env python3
"""Standalone REST smoke test for the Learning service.

The service is REST-only. Keep this executable check aligned with the public
HTTP contract instead of testing the retired Unix-domain socket transport.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICE_SCRIPT = Path(__file__).with_name("learning_service.py")


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def request_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=0.5) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    port = free_port()
    service_url = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(prefix="ce-learning-smoke-") as temp:
        temp_path = Path(temp)
        wrapper = f"""
import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, {str(ROOT)!r})
spec = importlib.util.spec_from_file_location("learning_service_smoke", {str(SERVICE_SCRIPT)!r})
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
module.HTTP_PORT = {port}
service = module.LearningService(Path({str(temp_path / 'unused.sock')!r}), Path({str(temp_path / 'learning')!r}))
service.thompson_client = None
service.start()
"""
        env = os.environ.copy()
        env["ENSEMBLE_LEARNING_HTTP_PORT"] = str(port)
        process = subprocess.Popen(
            [sys.executable, "-c", wrapper],
            cwd=ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("Learning service exited before becoming ready")
                try:
                    health = request_json(service_url + "/health")
                    if health.get("ok") is True:
                        break
                except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
                    time.sleep(0.05)
            else:
                raise RuntimeError("Learning REST service did not become ready")

            sys.path.insert(0, str(ROOT / "learning"))
            from learning_client import LearningClient

            client = LearningClient(service_url=service_url, timeout=1.0)
            ping = client._send_request({"op": "ping"})
            assert ping.get("ok") is True, ping

            accepted = client.process_outcome(
                task_id="rest-smoke-001",
                task_type="integration-test",
                model="haiku",
                rating=4,
                tokens=100,
                cost=0.0,
            )
            assert accepted is True, "Learning service did not acknowledge outcome"

            replay = client._send_request({
                "op": "process_outcome",
                "task_id": "rest-smoke-001",
                "task_type": "integration-test",
                "model": "haiku",
                "rating": 4,
                "tokens": 100,
                "cost": 0.0,
            })
            assert replay.get("ok") is True and replay.get("duplicate") is True, replay

            report = client.get_report()
            assert report.get("ok") is True, report
            assert report.get("total_outcomes", 0) >= 1, report
            print("PASS: Learning REST health, ping, outcome, idempotent replay, and report")
            return 0
        finally:
            process.terminate()
            try:
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2.0)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"FAIL: Learning REST smoke test: {exc}", file=sys.stderr)
        raise
