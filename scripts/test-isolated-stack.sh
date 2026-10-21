#!/usr/bin/env bash
# Run the CE service stack in an isolated temporary runtime. No systemd, profile,
# home-state, crontab, or existing CE service is touched.
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/ce-isolated-stack.XXXXXXXX")"
PIDS=()
cleanup() {
  local pid
  for pid in "${PIDS[@]:-}"; do kill "$pid" 2>/dev/null || true; done
  for pid in "${PIDS[@]:-}"; do wait "$pid" 2>/dev/null || true; done
  rm -rf "$TMP"
}
trap cleanup EXIT INT TERM

PYTHON="${ENSEMBLE_TEST_PYTHON:-python3}"
if [[ -n "${ENSEMBLE_TEST_VENV:-}" ]]; then
  PYTHON="$ENSEMBLE_TEST_VENV/bin/python"
  [[ -x "$PYTHON" ]] || { echo "FAIL: Python executable not found: $PYTHON" >&2; exit 2; }
else
  command -v "$PYTHON" >/dev/null 2>&1 || { echo "FAIL: Python executable not found: $PYTHON" >&2; exit 2; }
fi
read -r MEMORY_PORT GRAPH_PORT LEARNING_PORT THOMPSON_PORT GATEWAY_PORT < <("$PYTHON" - <<'PYPORTS'
import socket
sockets = []
try:
    for _ in range(5):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sockets.append(sock)
    print(*(sock.getsockname()[1] for sock in sockets))
finally:
    for sock in sockets:
        sock.close()
PYPORTS
)

export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
export ENSEMBLE_RUNTIME_DIR="$TMP/runtime"
export ENSEMBLE_LOG_DIR="$TMP/logs"
export ENSEMBLE_MEMORY_DIR="$TMP/data/memory"
export ENSEMBLE_LEARNING_DIR="$TMP/data/learning"
export ENSEMBLE_MEMORY_SOCKET="$TMP/runtime/memory.sock"
export ENSEMBLE_LEARNING_SOCKET="$TMP/runtime/learning.sock"
export ENSEMBLE_THOMPSON_SOCKET="$TMP/runtime/thompson.sock"
export ENSEMBLE_THOMPSON_STATE_FILE="$TMP/data/thompson-state.json"
export ENSEMBLE_GRAPH_STORE="$TMP/data/graph.json"
export ENSEMBLE_METRICS_FILE="$TMP/data/metrics.jsonl"
export ENSEMBLE_DECISION_PROVENANCE_FILE="$TMP/data/provenance.jsonl"
export ENSEMBLE_EXPERIMENT_FILE="$TMP/data/experiments.json"
export ENSEMBLE_MEMORY_HTTP_HOST=127.0.0.1
export ENSEMBLE_MEMORY_HTTP_PORT="$MEMORY_PORT"
export ENSEMBLE_GRAPH_HOST=127.0.0.1
export ENSEMBLE_GRAPH_PORT="$GRAPH_PORT"
export ENSEMBLE_LEARNING_HTTP_PORT="$LEARNING_PORT"
export ENSEMBLE_THOMPSON_HTTP_PORT="$THOMPSON_PORT"
export ENSEMBLE_HTTP_HOST=127.0.0.1
export ENSEMBLE_HTTP_PORT="$GATEWAY_PORT"
export ENSEMBLE_MEMORY_URL="http://127.0.0.1:$MEMORY_PORT"
export FLOSSWARE_MEMORY_URL="$ENSEMBLE_MEMORY_URL"
export ENSEMBLE_GRAPH_URL="http://127.0.0.1:$GRAPH_PORT"
export FLOSSWARE_GRAPH_URL="$ENSEMBLE_GRAPH_URL"
export ENSEMBLE_LEARNING_URL="http://127.0.0.1:$LEARNING_PORT"
export ENSEMBLE_THOMPSON_URL="http://127.0.0.1:$THOMPSON_PORT"

mkdir -p "$TMP/runtime" "$TMP/logs" "$TMP/data/memory" "$TMP/data/learning"
chmod 700 "$TMP" "$TMP/runtime" "$TMP/data"

start_service() {
  local name="$1" entry="$2"
  "$PYTHON" "$ROOT/$entry" >"$TMP/$name.log" 2>&1 &
  PIDS+=("$!")
}
start_service thompson thompson-service/thompson_service.py
start_service memory memory-service/memory_service.py
start_service graph graph-service/graph_service.py
start_service learning learning-service/learning_service.py
start_service gateway server/ensemble_server.py

fail_with_logs() {
  echo "FAIL: $1" >&2
  local f
  for f in thompson memory graph learning gateway; do
    echo "===== $f.log =====" >&2
    cat "$TMP/$f.log" >&2 || true
  done
  exit 1
}

# Verify the gateway process itself stays alive before trusting any HTTP response.
sleep 1
kill -0 "${PIDS[4]}" 2>/dev/null || fail_with_logs "gateway exited during startup"
if ! grep -q "listening on http://127.0.0.1:$GATEWAY_PORT" "$TMP/gateway.log"; then
  fail_with_logs "gateway did not confirm its expected listener (port $GATEWAY_PORT)"
fi

wait_http() {
  local url="$1" attempts="${2:-30}" i
  for ((i=0; i<attempts; i++)); do
    if curl --silent --show-error --fail --max-time 1 "$url" >/dev/null 2>&1; then return 0; fi
    sleep 0.25
  done
  return 1
}
wait_http "$ENSEMBLE_MEMORY_URL/health" || fail_with_logs "Memory service did not become healthy"
wait_http "$ENSEMBLE_GRAPH_URL/health" || fail_with_logs "Graph service did not become healthy"
wait_http "$ENSEMBLE_LEARNING_URL/health" || fail_with_logs "Learning service did not become healthy"
wait_http "$ENSEMBLE_THOMPSON_URL/health" || fail_with_logs "Thompson service did not become healthy"
wait_http "http://127.0.0.1:$GATEWAY_PORT/api/v1/ready" || fail_with_logs "gateway readiness failed"

READY="$TMP/ready.json"
curl --silent --show-error --fail --max-time 3 "http://127.0.0.1:$GATEWAY_PORT/api/v1/ready" -o "$READY" || fail_with_logs "readiness request failed"
"$PYTHON" - "$READY" <<'PY' || fail_with_logs "readiness JSON contract failed"
import json, sys
with open(sys.argv[1], encoding="utf-8") as f:
    data = json.load(f)
if data.get("ok") is not True or data.get("ready") is not True:
    raise SystemExit(f"gateway is not ready: {json.dumps(data, indent=2)}")
print("PASS: gateway readiness JSON and all dependencies")
PY

PROBLEMS="$TMP/problems.json"
STATUS="$(curl --silent --show-error --max-time 5 -o "$PROBLEMS" -w '%{http_code}' "http://127.0.0.1:$GATEWAY_PORT/api/v1/decision/query/problems")" || fail_with_logs "decision endpoint request failed"
"$PYTHON" - "$PROBLEMS" "$STATUS" <<'PY' || fail_with_logs "decision endpoint JSON contract failed"
import json, sys
with open(sys.argv[1], encoding="utf-8") as f:
    data = json.load(f)
status = int(sys.argv[2])
if status not in (200, 422):
    raise SystemExit(f"unexpected decision endpoint HTTP {status}: {json.dumps(data, indent=2)}")
if not isinstance(data, dict):
    raise SystemExit("decision endpoint JSON root must be an object")
print(f"PASS: decision endpoint returned JSON (HTTP {status})")
PY

echo "PASS: isolated CE stack acceptance"
echo "Ports used: gateway=$GATEWAY_PORT memory=$MEMORY_PORT graph=$GRAPH_PORT learning=$LEARNING_PORT thompson=$THOMPSON_PORT"
