# CE diagnostics contract

CE has two complementary diagnostic entry points:

- `tools/ce doctor` checks local configuration and runtime readiness.
- `tools/ce diagnose` probes service operations and selected integration paths.

Both are bounded and avoid model calls. A successful health response is not proof that a complete user workflow works, and a successful diagnostic only proves the checks it actually ran.

## Doctor: configuration and readiness

Run from a source checkout:

```bash
bash tools/ce doctor
bash tools/ce doctor --verbose
bash tools/ce doctor --json
```

The default human-readable view emphasizes failures, warnings, and skipped checks. `--verbose` includes passing checks. JSON output has this shape:

```json
{
  "ok": true,
  "summary": {
    "passed": 0,
    "failed": 0,
    "warnings": 0,
    "skipped": 0
  },
  "checks": [
    {
      "code": "GATEWAY_HEALTH",
      "title": "Gateway REST health",
      "status": "pass",
      "severity": "info",
      "detail": "HTTP 200; JSON ok=true",
      "remediation": "",
      "duration_ms": 12
    }
  ]
}
```

The counts above are illustrative, not an expected result. Each check has a stable code, status, detail, remediation, and duration. Status is one of `pass`, `fail`, `warn`, or `skip`. Severity is `error`, `warning`, or `info`. The process exits nonzero when at least one check fails; warnings and skipped checks remain visible but do not alone make it fail.

Doctor checks the Claude Code executable, explicitly selected profile and managed hook configuration, provider identity for that profile, the CE gateway unit where systemd is available, and configured Memory/gateway/capability health endpoints. It does not print credentials. It never implicitly inspects the ordinary `~/.claude` profile: set `ENSEMBLE_CLAUDE_CODE_CONFIG_DIR` or `CLAUDE_CONFIG_DIR` to select a profile explicitly. `ENSEMBLE_CLAUDE_CODE_EXPECTED_PROVIDER` controls the expected provider; the default is `vertex`.

## Service dependency graph

The supported runtime path uses existing interfaces rather than introducing a broker:

- **Prompt retrieval:** Claude Code UserPromptSubmit hook calls Memory REST /memory/search; when cwd is present, it also queries Graph REST for the project node and bounded one-hop relationships.
- **Gateway-backed checks:** gateway REST reaches configured Memory and Graph health/operation endpoints. Gateway readiness reports its configured core dependencies.
- **Learning outcome path:** Learning Unix socket uses Memory capture/acknowledgement and a Thompson Unix-socket client. --deep or --pipeline-test verifies this path with isolated temporary state rather than changing production learner state.

A diagnostic only proves the operations it actually exercised. Standard mode primarily checks health/ping contracts; --deep adds read operations and the isolated Learning-to-Thompson integration. Neither mode proves an unexercised capability such as real provider invocation or external reviewer delivery.

## Diagnose: service and integration checks

Read-only service checks:

```bash
bash tools/ce diagnose --json
bash tools/ce diagnose --deep --json
bash tools/ce diagnose --service memory --json
bash tools/ce diagnose --service gateway --timeout 3 --json
```

Supported service selectors are `gateway`, `memory`, `graph`, `decision`, `learning`, and `thompson`. Per-request timeouts must be between 0.1 and 10 seconds.

- Standard mode checks selected gateway/service endpoints or sockets.
- `--deep` adds read operations and, for a full-stack run, an isolated Learning-to-Thompson pipeline test using temporary state and sockets.
- `--pipeline-test` explicitly runs that isolated pipeline test.
- `--hook-smoke` runs the installed UserPromptSubmit hook against configured Memory/Graph services. It does not invoke a model.
- `--prompt-context-smoke` requires emitted context and at least one Knowledge result. It does not prove Claude Code delivered the context to a model.
- `--session-end-smoke` also runs SessionEnd capture. This appends one synthetic metadata-only event to production Memory, so it requires the additional explicit `--allow-memory-write` flag.

Hook selection is isolated from unrelated Claude profiles. Lookup order is `CLAUDE_HOOK_DIR`, then the `hooks/` directory under `ENSEMBLE_CLAUDE_CODE_CONFIG_DIR` or `CLAUDE_CONFIG_DIR`, then the standalone CE profile at `~/.FlossWare/claude-ensemble/shared/claude-code/hooks`. Use `--hook-dir` only when intentionally testing a different profile.

The diagnose JSON report has this shape:

```json
{
  "ok": true,
  "correlation_id": "generated-uuid",
  "checks": 1,
  "passed": 1,
  "failed": 0,
  "warnings": 0,
  "skipped": 0,
  "mode": "standard",
  "side_effects": "read-only service probes; no production writes, model calls, or notifications",
  "results": [
    {
      "step": "gateway.liveness",
      "status": "pass",
      "endpoint": "GET http://127.0.0.1:8080/api/v1/health",
      "duration_ms": 12,
      "correlation_id": "generated-uuid",
      "detail": "HTTP 200; ok=true",
      "evidence": "HTTP 200; ok=true",
      "remediation": ""
    }
  ]
}
```

Values are illustrative. Each result includes a stable step name, status, endpoint or socket, duration, evidence summary, and remediation. The generated correlation ID is included in the HTTP request headers and socket requests where supported. Status values are `pass`, `fail`, `warn`, or `skip`. The process exits nonzero when a check fails; warnings and skips are counted and reported separately.

## Safety and interpretation

- Neither command invokes a model by default. They do not send prompts or source code to model providers or external reviewers.
- Standard diagnose checks are read-only. Deep mode's Learning-to-Thompson exercise uses isolated temporary state.
- Hook smoke executes the selected installed hook directly. Prompt-context smoke remains a hook/service test, not proof of model receipt.
- SessionEnd smoke is the one explicit exception that writes a metadata-only synthetic event to production Memory. It is opt-in and requires `--allow-memory-write`; it does not publish a message or send an alert.
- A skipped check means it was not verified, not that it passed. A warning is not equivalent to a clean pass.
- Passing automated tests and diagnostics do not replace the separate release gate in [issue #505](https://github.com/FlossWare/claude-ensemble/issues/505), including trusted-host acceptance. Do not treat host staging or activation as complete until that work is explicitly authorized and its evidence is recorded.

## Memory REST Acceptance Testing (Phase 5)

The Memory REST service listens by default on `http://127.0.0.1:8767` (or port configured via `ENSEMBLE_MEMORY_HTTP_PORT`).

### 1. Direct Curl Commands

**Health / Readiness Check:**
```bash
curl -s -f http://127.0.0.1:8767/health
```

**Seed Disposable Test Memory:**
```bash
curl -s -X POST http://127.0.0.1:8767/memory/write \
  -H "Content-Type: application/json" \
  -d '{"name": "acceptance_test_memory", "content": "# Acceptance Test\nClaude Ensemble Memory REST integration contract."}'
```

**Retrieve Test Memory via Search API:**
```bash
curl -s -X POST http://127.0.0.1:8767/memory/search \
  -H "Content-Type: application/json" \
  -d '{"query": "REST integration contract", "limit": 10, "source": "memory"}'
```

**Read Memory directly:**
```bash
curl -s -X POST http://127.0.0.1:8767/memory/read \
  -H "Content-Type: application/json" \
  -d '{"name": "acceptance_test_memory"}'
```

**Append Idempotent Event:**
```bash
curl -s -X POST http://127.0.0.1:8767/memory/append-once \
  -H "Content-Type: application/json" \
  -d '{"name": "session_events", "event_id": "test-evt-001", "entry": {"event_type": "acceptance_check", "session_id": "test-session"}}'
```

**Cleanup Disposable Test Memory:**
Remove `acceptance_test_memory.md` from the configured memory state directory (`ENSEMBLE_MEMORY_DIR` / `$HOME/.FlossWare/claude-ensemble-state/data/memory`).

### 2. Automated Hook Acceptance Command

Run the following command to invoke the actual CE `UserPromptSubmit` hook and verify context emission:

```bash
FLOSSWARE_MEMORY_URL="${FLOSSWARE_MEMORY_URL:-http://127.0.0.1:8767}" node -e '
const { spawn } = require("child_process");
const prompt = process.argv[1] || "REST integration contract";
const child = spawn(process.argv[0], ["hooks/memory-search-on-prompt.js"], {
  env: { ...process.env },
  stdio: ["pipe", "pipe", "pipe"]
});
let stdout = "", stderr = "";
child.stdout.on("data", d => stdout += d);
child.stderr.on("data", d => stderr += d);
child.stdin.end(JSON.stringify({ hook_event_name: "UserPromptSubmit", prompt }));
child.on("close", code => {
  if (code !== 0) {
    console.error("Hook process exited with error code", code);
    process.exit(2);
  }
  if (stderr.includes("Memory search unavailable")) {
    console.error("ACCEPTANCE FAILURE: Memory REST service is unavailable");
    process.exit(1);
  }
  if (!stdout.trim()) {
    console.log("ACCEPTANCE RESULT: Successful request, but no matching memory found (empty context)");
    process.exit(3);
  }
  try {
    const payload = JSON.parse(stdout);
    const context = payload?.hookSpecificOutput?.additionalContext;
    if (typeof context === "string" && context.trim().length > 0) {
      console.log("ACCEPTANCE SUCCESS: Context injected into prompt context:\n" + context);
      process.exit(0);
    } else {
      console.error("ACCEPTANCE FAILURE: Malformed payload or missing context");
      process.exit(2);
    }
  } catch (err) {
    console.error("ACCEPTANCE FAILURE: Output is not valid JSON:", err.message);
    process.exit(2);
  }
});
' "REST integration contract"
```

Exit status codes:
- `0`: Successful retrieval and prompt context injection.
- `1`: Service unavailable (Memory REST daemon not running or unreachable).
- `2`: HTTP or contract failure / malformed output.
- `3`: Successful request but no matching memory records found.
