#!/usr/bin/env python3
"""Verify the baseline systemd service ownership contract."""

from pathlib import Path
import os
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]

EXPECTED_SERVICES = {
    "memory-service/claude-memory.service.template",
    "thompson-service/claude-thompson.service.template",
    "learning-service/claude-learning.service.template",
    "graph-service/claude-graph.service.template",
}


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


for relative_path in EXPECTED_SERVICES:
    path = ROOT / relative_path
    if not path.is_file():
        fail(f"missing service template: {relative_path}")

    text = path.read_text(encoding="utf-8")

    if "[Service]" not in text:
        fail(f"{relative_path}: missing [Service] section")

    if not re.search(r"(?m)^Type=simple\s*$", text):
        fail(f"{relative_path}: service is not Type=simple")

    if not re.search(r"(?m)^ExecStart=.+\S", text):
        fail(f"{relative_path}: missing service-owned ExecStart")

    if re.search(r"(?mi)^ExecStart=.*(?:systemctl|start-stop-daemon)\b", text):
        fail(f"{relative_path}: ExecStart delegates lifecycle to another supervisor")

    if re.search(r"(?mi)^ExecStart=.*(?:ensemble_server|orchestrate)\b", text):
        fail(f"{relative_path}: ExecStart uses an orchestration process")

    if re.search(r"(?mi)\bsystemctl\s+--user\s+(?:start|restart|stop)\b", text):
        fail(f"{relative_path}: template starts/stops sibling services")

learning = (ROOT / "learning-service/claude-learning.service.template").read_text(
    encoding="utf-8"
)
if "After=network.target claude-thompson.service" not in learning:
    fail("Learning must declare ordering after Thompson")
if "Wants=claude-thompson.service" not in learning:
    fail("Learning must declare its Thompson dependency")

graph = (ROOT / "graph-service/claude-graph.service.template").read_text(
    encoding="utf-8"
)
if "EnvironmentFile=-%h/.config/claude-ensemble/environment" not in graph:
    fail("Graph must support the optional user environment file")
if 'Environment="ENSEMBLE_GRAPH_HOST=127.0.0.1"' not in graph:
    fail("Graph must pin its bind address to loopback")
if graph.index("EnvironmentFile=") > graph.index('Environment="ENSEMBLE_GRAPH_HOST=127.0.0.1"'):
    fail("Graph loopback bind must be declared after the optional environment file")
if "systemctl --user" in graph:
    fail("Graph template must not supervise sibling services")

for relative_path in EXPECTED_SERVICES:
    text = (ROOT / relative_path).read_text(encoding="utf-8")
    if text.count("[Service]") != 1:
        fail(f"{relative_path}: expected exactly one [Service] section")

# The venv runner must be syntactically valid and export isolated defaults.
syntax = subprocess.run(["bash", "-n", str(ROOT / "tools/ce")], capture_output=True, text=True)
if syntax.returncode:
    fail(f"tools/ce has invalid shell syntax: {syntax.stderr}")
with tempfile.TemporaryDirectory(prefix="ce-dev-env-test-") as home:
    env = os.environ.copy()
    env["HOME"] = home
    env.pop("ENSEMBLE_CE_DEV_STATE", None)
    env.pop("ENSEMBLE_CE_DEV_VENV", None)
    result = subprocess.run(
        ["bash", str(ROOT / "tools/ce"), "dev", "env"],
        env=env, capture_output=True, text=True, check=False,
    )
    if result.returncode:
        fail(f"tools/ce dev env failed: {result.stderr}")
    expected_profile = Path(home) / ".FlossWare" / "claude-ensemble-profile"
    expected_state = Path(home) / ".FlossWare" / "claude-ensemble-state"
    for expected in (
        f"export CLAUDE_CONFIG_DIR={expected_profile}",
        f"export ENSEMBLE_MEMORY_DIR={expected_state / 'data/memory'}",
        "export ENSEMBLE_GATEWAY_URL=http://127.0.0.1:18080",
    ):
        if expected not in result.stdout:
            fail(f"development environment omitted expected setting: {expected}")

print("PASS: service lifecycle ownership contract")


# The manual fallback is documented as explicit foreground commands, not a
# hidden process supervisor. Keep this list aligned with the actual service entrypoints.
lifecycle_docs = (ROOT / "SERVICE_LIFECYCLE.md").read_text(encoding="utf-8")
manual_services = {
    "memory": "memory-service/memory_service.py",
    "thompson": "thompson-service/thompson_service.py",
    "learning": "learning-service/learning_service.py",
    "graph": "graph-service/graph_service.py",
    "gateway": "server/ensemble_server.py",
}
runner = (ROOT / "tools/ce").read_text(encoding="utf-8")
for service, entrypoint in manual_services.items():
    if f"tools/ce dev run {service}" not in lifecycle_docs:
        fail(f"venv-based development instructions missing service: {service}")
    if f'{service}) target="{entrypoint}"' not in runner:
        fail(f"development runner does not map {service} to {entrypoint}")
if "use Ctrl-C to stop its service" not in lifecycle_docs:
    fail("manual foreground instructions must document explicit shutdown")
if "CE clients do not silently start a replacement daemon" not in lifecycle_docs:
    fail("unavailable-service behavior must explicitly prohibit hidden startup")
if "MemoryClient.append()` returns `False`" not in lifecycle_docs:
    fail("offline Memory append must document a False return value")
if "no automatic replay or flush" not in lifecycle_docs:
    fail("offline Memory cache must document that callers need to retry")
