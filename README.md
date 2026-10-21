# Claude Ensemble

**Claude Code orchestration with persistent learning and service integration.**

Claude Ensemble is a portable toolkit built around the installed Claude Code runtime. It combines multi-phase worker/arbiter review, persistent Memory, autonomous outcome learning, Thompson/GA tuning, cost tracking, compression, caching, and reusable workflow skills. The active runtime routes model calls through Claude Code. Direct API provider implementations and API-key rotation have been removed from this branch.

It is designed to work both in Red Hat-centric environments and as a standalone personal or open-source toolkit. Runtime paths, credentials, service sockets, and repository locations are configurable rather than hard-coded.

---

## CE Service Boundary

The managed runtime consists of the gateway plus four application services: **Memory, Thompson, Graph, and Learning**. Alert and Messenger are legacy components; new installs do not activate them, and upgrades retire their old systemd units. SessionEnd capture writes metadata to Memory directly and does not depend on a message bus.

For a source-checkout install, `install.sh` defaults to the isolated profile at `~/.FlossWare/claude-ensemble-profile` and service data at `~/.FlossWare/claude-ensemble-state`. The versioned deployment uses `~/.FlossWare/claude-ensemble/shared/claude-code` for its profile. Neither mode should modify the ordinary `~/.claude` profile unless the user explicitly overrides the profile path.

## What's Inside

### Core Infrastructure
- **Memory Service** — Optional central authority for concurrent session access
- **Thompson Router** — Intelligent model selection based on learned performance
- **Autonomous Learning** — Self-improvement from real task outcomes
- **Portable Learning Artifacts** — Versioned, provider-neutral learning records that can move independently between CE environments and become candidates for Loom
- **Arbitration Orchestrator** — Multi-phase worker/arbiter pattern for critical decisions
- **Graph Service** — Loopback-only durable Graph HTTP service managed by systemd

### Optimization & Analysis
- **GA Tuning** — Genetic algorithm optimization every 4 hours (synthetic, zero cost)
- **Model Runtime** — Uses Claude Code authentication and model aliases (haiku, sonnet, opus)
- **Cost Tracking** — JSONL audit log of all API usage
- **5 Dashboards** — Cost, Thompson routing, autonomous learning, GA tuning, performance

### Workflow Skills (Learning-Integrated)
- **Code PR Review** (`code-pr-review`) — Worker/arbiter code review through the single Claude Code runtime
- **Documentation** (`code-doc`) — Auto-generate docs, learns which models write better
- **Release Notes** (`code-release-notes`) — Generate release notes from commits using the configured Claude Code runtime
- **Autonomous Variants** (`code-pr-review-auto`, `code-doc-auto`) — Run without user confirmation

Workflow outcomes pass through the Learning service only when they have durable, evidence-backed evaluation artifacts. Learning owns persistence, deduplication, and Thompson updates; generic session completion is never treated as a quality rating.

### Capabilities  
- **Compression** — Text compression support; reduction depends on input and must be measured per workload
- **Caching** — Prompt caching framework with hit/miss tracking (Phase 1 ready)
- **Hooks** — Memory search on prompt submit, learning extraction
- **Memory** — Project memory files (feedback, projects, references)

---

## Quick Start

### Installation

The versioned installer is the deployment path for a standalone CE runtime. Do not activate a release on a real host until the staging-only checks and acceptance procedure in [issue #505](https://github.com/FlossWare/claude-ensemble/issues/505) have passed. It stores immutable releases under `~/.FlossWare/claude-ensemble/releases/`, selects the active release through the atomic `current` symlink, and keeps mutable state under `shared/`. The runtime release tree contains no Git metadata and must not point into a development checkout.

**First, perform a structural-only staging run.** Use a clean source checkout outside a development checkout's symlinked paths and choose a new, disposable staging root:

```bash
SOURCE="$HOME/.FlossWare/ce-source"
STAGE="$HOME/.FlossWare/ce-staging-test"
git clone https://github.com/FlossWare/claude-ensemble.git "$SOURCE"
test ! -e "$STAGE"
ENSEMBLE_INSTALL_ROOT="$STAGE" ENSEMBLE_STAGING_ONLY=1 \
  bash "$SOURCE/install-standalone.sh" 0.1 "$SOURCE"
```

Staging-only mode checks release structure and writes `deployment.json`. It does **not** activate the release, create `current`, migrate Claude profiles, invoke systemd, or alter running services. Pick a fresh `SOURCE` and `STAGE` if either path already exists.

For an ordinary install, the no-version form selects the numerically newest published `MAJOR.MINOR` Git tag and downloads that exact tagged commit. Explicit version selection remains available for staging, rollback, and controlled testing. A tag must be pushed through the normal release process before the default installer can see it.

After reviewing the staged tree and manifest, a **separate non-staging install activates the selected release and controls the five CE user services**:

```bash
bash "$SOURCE/install-standalone.sh"
```

The default deployment root is `~/.FlossWare/claude-ensemble`. Activation snapshots the existing service units and active-service state, validates the release, switches `current` atomically, starts services in dependency order, and checks both service activity and gateway readiness. A failed activation attempts to restore the previous release. Do not run the activation command against a disposable staging root as a substitute for testing on an isolated system: activation controls the current user's systemd units and checks the configured gateway endpoint.

The deployment keeps mutable CE Claude Code configuration in `~/.FlossWare/claude-ensemble/shared/claude-code/`; ordinary `~/.claude/` is not the CE profile and is left untouched. The previous known-good release is recorded separately from staged or failed releases. Every non-staging versioned install or upgrade first writes a private recovery snapshot outside the deployment root under `~/.FlossWare/claude-ensemble-backups/`; if the snapshot fails, installation stops. See [Profiles, installation modes, and backups](docs/PROFILES_AND_BACKUPS.md) for profile boundaries, verification, backup contents, and recovery guidance.

**Upgrade an existing deployment** by selecting a new MAJOR.MINOR release. Never overwrite an existing release directory; it is immutable. The upgrade command requires a valid active release, stages and validates the new release, then activates it with health checks and automatic rollback on failure:

```bash
bash "$SOURCE/upgrade.sh" 0.2 "$SOURCE"
```

Choose the next unused release label rather than copying the example blindly. To inspect state or explicitly roll back, use the active release's deployment manager as described below.

The scheduled GA job follows the same isolation boundary. Its results, pending learning artifacts, ingestion receipts, evolution log, lock, and scheduler log live under `~/.FlossWare/claude-ensemble/shared/data/ga-tuning/` and `shared/logs/`; it reads the CE profile at `shared/claude-code/settings.json`, not ordinary `~/.claude/settings.json`. Once deployment acceptance is complete, the crontab entry should call the stable release path:

The GA dashboard automatically reads the installed deployment's shared results when a versioned `current` release exists. Direct development runs retain the repository-local `ga_tuning/results/` default. Set `CE_GA_RESULTS_DIR` to override the result directory explicitly.

```cron
0 */4 * * * "$HOME/.FlossWare/claude-ensemble/current/tools/ga-tuning-schedule.sh"
```

Do not migrate or run the host crontab as part of staging. The existing `~/.claude/ga-tuning-schedule.sh` entry remains a host-side migration item until the isolated release has passed acceptance.

Inspect deployment state and use explicit rollback/pruning commands through the selected release's deployment manager:

```bash
ROOT="$HOME/.FlossWare/claude-ensemble"
bash "$ROOT/current/tools/ce-deployment" status
bash "$ROOT/current/tools/ce-deployment" rollback
bash "$ROOT/current/tools/ce-deployment" prune
```

Rollback selects the recorded previous known-good release, not simply the highest version number. Pruning retains the active and previous known-good releases and refuses to proceed when activation history is missing or inconsistent. If an activation is interrupted, inspect the journal and run `recover` from any retained release, even if `current` is missing: set `VERSION` to an existing release (for example `0.1`) and run `bash "$ROOT/releases/$VERSION/tools/ce-deployment" recover` before attempting another activation.

For a deployment built from an existing checkout, pass that checkout as the second argument, as shown above. Without a checkout argument, the installer downloads the current source archive. The root `install.sh` is the per-release runtime installer used by the deployment manager, not the supported first-install or upgrade entry point. Use `install-standalone.sh`, `upgrade.sh`, and `uninstall.sh` for the deployment lifecycle.

The versioned installer:
1. Keeps immutable release trees under `~/.FlossWare/claude-ensemble/releases/` without Git metadata
2. Selects the active release through `current` and retains the active and previous known-good release
3. Keeps mutable CE configuration and profile state under `shared/`, without modifying ordinary `~/.claude/`
4. Refuses broken links, links outside the deployment root, and inconsistent known-good history
5. Writes a deployment manifest and preserves the previous release when activation fails

The `/ce-review` skill runs the worker/arbiter code review without Jira; `/ce-jira-review` adds Jira acceptance criteria; `/ce-solve` investigates and implements a coding fix; `/ce-status` reports profile and service status; `/ce-diagnose` runs deeper diagnostics; `/ce-memory` distinguishes Memory from durable Knowledge; `/ce-learning` inspects learning and Thompson outcomes; and `/ce-ga` inspects genetic-algorithm tuning evidence. Diagnostic skills default to read-only. `/ce-solve` may edit the current repository but does not commit, push, merge, deploy, or perform destructive operations without authorization. All skills use the authenticated Claude Code runtime and must not send proprietary source to external reviewer providers. To install the full set into an explicitly selected CE profile, run:

```bash
ENSEMBLE_CLAUDE_CODE_CONFIG_DIR="$HOME/.config/claude-ensemble/acceptance-profile" \
  bash tools/install-ce-skills.sh
```

Replace the example profile path with the profile Claude Code actually uses. Individual installers are available as `tools/install-ce-<name>-skill.sh`. The aggregate installer is idempotent, honors the selected profile, installs all eight skills, and preserves conflicting user-owned skill files with a warning.

**See [CREDENTIALS_SETUP.md](CREDENTIALS_SETUP.md)** for credential handling. Claude Code owns model authentication; optional integrations may require their own credentials. CE loads its configured credentials file only when the supported session initialization is sourced.

### Session Initialization
On startup, `scripts/ensemble-init.sh` automatically:
1. Connects to memory service (systemd daemon)
2. Initializes autonomous learning
3. Refreshes the local supported-model inventory (haiku, sonnet, opus); this is not live third-party API discovery
4. Prints toolkit status

```bash
✓ Connected to memory service
✓ Autonomous learner ready
✓ Claude Ensemble Toolkit initialized
  Tools: compression, caching, cost_tracking, ga_tuning, thompson_router, arbitration
  Memory: ~/.FlossWare/claude-ensemble-state/data/memory (source-checkout install)
  Arbitration: multi-phase orchestrator for critical decisions
  Autonomous learning: Thompson continuously improving from real tasks
```

### Diagnose the running CE services

See [the diagnostics contract](docs/DIAGNOSTICS.md) for the command reference, JSON schemas, status/exit semantics, profile selection, and side-effect boundaries.

Run a correlated health and integration check before testing a live Claude Code session:

```bash
bash tools/ce diagnose --deep --json
```

This probes the configured gateway, Memory, Graph, decision endpoint, Learning, and Thompson services. Deep mode also runs the real Learning-to-Thompson request path with temporary state and sockets. It does not call a model or write to production Learning/Thompson state.

To exercise the installed `UserPromptSubmit` hook against live Memory/Graph services without writing production data:

```bash
bash tools/ce diagnose --hook-smoke --json
```

Hook lookup follows `CLAUDE_HOOK_DIR`, then the explicitly selected `ENSEMBLE_CLAUDE_CODE_CONFIG_DIR` or `CLAUDE_CONFIG_DIR`. With none configured, it uses the standalone CE profile at `~/.FlossWare/claude-ensemble/shared/claude-code/hooks`. It does **not** silently inspect ordinary `~/.claude/hooks`; pass `--hook-dir` explicitly when intentionally testing another profile.

To also exercise installed SessionEnd capture, explicitly authorize one uniquely identified metadata-only event in production Memory:

```bash
bash tools/ce diagnose --session-end-smoke --allow-memory-write --json
```

The SessionEnd smoke test sends no transcript, prompt text, tool output, or project path. The explicit flag is required because the test writes one event to production Memory.

For a read-only service check without the isolated pipeline test:

```bash
bash tools/ce diagnose --json
```

### Run Dashboards
After doing real work, view your performance:

```bash
cost-dashboard.py              # Spending by model/provider/workflow/day
thompson-dashboard.py          # Routing accuracy, model rankings, quality
autonomous-learning-dashboard.py  # Learning progress, outcomes
ga-tuning-dashboard.py         # Fitness trends, parameter evolution
```

### Workflow Skills
For routine tasks with learning integration:

```bash
# Interactive PR review (asks before approve/reject)
/code-pr-review

# Autonomous PR review (auto-approves/rejects)
/code-pr-review-auto

# Interactive documentation generation
/code-doc

# Autonomous doc generation
/code-doc-auto

# Generate release notes from commits
/code-release-notes
```

See [Workflow Learning Contract](docs/WORKFLOW_LEARNING_CONTRACT.md) for the current evidence requirements and Thompson update boundary.

### Multi-Phase Arbitration
For critical decisions (security, breaking changes, complex bugs):

```bash
arbitrate code-review /path/to/repo --phases 3 --context-dir src/api
arbitrate bug-analysis error.log code.py --phases 2
arbitrate security-audit src/ --phases 3
```

Each stage runs multiple independent workers followed by an arbiter. The arbiter adjudicates the worker results, and the complete execution/review state is handed to the next configured stage. Model labels may be reused across stages.


### Autonomous Collaboration

For open-ended engineering problems, the collaboration loop separates **idea generation** from **independent review** and **adjudication**:

1. CE asks multiple configured solver models for independent solutions.
2. Grok and Perplexity review each candidate independently.
3. Jules can also review the proposal against an allowlisted repository branch.
4. The arbiter selects the most-supported solution from the evidence rather than majority voting.
5. The arbiter can request targeted reviewer follow-ups.
6. The loop revises and re-reviews until the explicit completion criteria are met or a human decision is genuinely required.
7. The complete candidates, reviews, adjudications, rejected alternatives, and audit events remain in the collaboration result.

The canonical REST entry point is:

```text
POST /api/v1/collaboration/run
```

Example:

```json
{
  "task": "Design a safe way to add autonomous multi-reviewer collaboration.",
  "constraints": ["Do not replace Claude Code", "Preserve provider-neutral contracts"],
  "context": "Relevant repository architecture and current implementation",
  "solvers": ["sonnet", "haiku"],
  "arbiter": "opus",
  "reviewers": ["grok", "perplexity", "jules"],
  "max_rounds": 3,
  "max_solver_calls": 6,
  "max_review_calls": 18,
  "max_arbiter_calls": 6
}
```

The reviewer MCP service must be running for external reviewer legs. Jules proposal review additionally requires `REVIEWER_MCP_REPOSITORY` to name an allowlisted repository and optionally `REVIEWER_MCP_BRANCH` to select its branch. Credentials remain environment-only.

The collaboration REST endpoint is deliberately protected by a deployment-owned policy. Configure `ENSEMBLE_COLLABORATION_AUTH_TOKEN` before enabling it. Caller-selected solvers, arbiter, and reviewers must be present in the server-owned allowlists `ENSEMBLE_COLLABORATION_SOLVERS`, `ENSEMBLE_COLLABORATION_ARBITER`, and `ENSEMBLE_COLLABORATION_REVIEWERS`. External reviewer transmission also requires explicit `ENSEMBLE_COLLABORATION_ALLOW_EXTERNAL_DATA=true`. Server-side ceilings are controlled by `ENSEMBLE_COLLABORATION_MAX_ROUNDS`, `ENSEMBLE_COLLABORATION_MAX_SOLVER_CALLS`, `ENSEMBLE_COLLABORATION_MAX_REVIEW_CALLS`, and `ENSEMBLE_COLLABORATION_MAX_ARBITER_CALLS`; request values can only lower those ceilings. The default external reviewer allowlist is empty, so arbitrary callers cannot cause candidate/context data to leave the gateway.

This layer is an orchestration capability, not a replacement for Claude Code. Reviewer transport remains in `reviewer-mcp/`; arbitration and collaboration state remain in CE's orchestration layer.

---

## Canonical REST Integration

Claude Ensemble has one client-facing REST boundary at `127.0.0.1:8080` by default:

- `/api/v1/graph/*` routes to the independently managed Graph REST service.
- `/api/v1/memory/*` routes to the independently managed Memory REST service.
- `/api/v1/decision/*` exposes Decision Support through the same public boundary.
- `/api/v1/models/*` remains the model inventory and invocation surface; Claude Code owns authentication, so CE does not expose or rotate provider API keys.
- `/api/v1/capabilities` exposes stable model/provider capabilities for routing decisions, including credential readiness.
- `/api/v1/capabilities/health` exposes observed operational health from existing credentials, Thompson outcomes, and cost records. A state of `unknown` means there is not enough observed history to establish operational health.
- `/api/v1/experiments/evaluate` evaluates a small baseline-versus-variant experiment and stores its versioned JSONL result; `/api/v1/experiments/{id}` retrieves the latest result. Evaluation is deterministic: each metric votes for baseline or variant according to its higher/lower direction, metric ties score no vote, and the overall winner is the unweighted majority of non-tied metric winners.
- `/api/v1/metrics` exposes the append-only operational metrics stream; `/api/v1/metrics/aggregate` provides small service/model/status aggregates. The canonical record is dependency-free JSONL and can be exported to CSV for spreadsheets or pandas. Model invocation records are emitted by the existing provider client when a metrics store is configured.
- Other services may be registered with `ENSEMBLE_<SERVICE>_URL` without changing the public contract.

Each service URL may point either to the concrete service or to another Claude Ensemble REST gateway. If the configured target identifies itself as a Claude Ensemble gateway through `/api/v1/health`, the local gateway transparently forwards the full `/api/v1/<service>/*` contract to that remote instance. This means multiple Ensemble instances can cooperate without a separate federation protocol.

For example:

```bash
export ENSEMBLE_GRAPH_URL=http://farawayhost:8080
export ENSEMBLE_MEMORY_URL=http://localhost:8767
```

Clients still use:

```text
POST /api/v1/graph/add-node
```

The gateway handles local service URLs directly and remote Ensemble URLs through HTTP/REST. Forwarding preserves the request method, path semantics, body, and relevant headers. A service URL pointing back to the current gateway is rejected to prevent forwarding loops. The remote forwarding header policy is intentionally narrow: content type, accept, request/correlation IDs, and the gateway forwarding marker are propagated; client credentials such as `Authorization` and cookies are not. Gateways append their own identity to `X-Ensemble-Forwarded` and enforce a maximum hop count to terminate multi-gateway cycles.

Services may run independently, but service integration uses REST/HTTP contracts rather than implementation imports or shared storage. The gateway does not supervise service lifecycles.

## Architecture

### REST Gateway (Systemd Daemon)
- **Path:** `server/ensemble_server.py`
- **Status:** Installed and verified by the top-level installer as `claude-ensemble.service`
- **Endpoint:** `127.0.0.1:8080` by default
- **Dependencies:** Starts after the Graph and Memory services; communicates with them over loopback HTTP
- **Diagnostics:** `tools/claude-config/bin/claude-config doctor` reports the systemd unit state separately from HTTP health at `/api/v1/health`

### Graph Service (Systemd Daemon)
- **Path:** `graph-service/`
- **Status:** Independently managed by the systemd user service `claude-graph.service`
- **Endpoint:** `127.0.0.1:8766`
- **Storage:** `~/.local/share/claude-ensemble/graph.json` by default
- **Function:** Durable Graph REST boundary; it does not supervise sibling services

### Memory Service (Optional Systemd Daemon)
- **Path:** `memory-service/`
- **Status:** Running (auto-start on login)
- **Port:** Per-user Unix socket under the configured Claude Ensemble runtime directory
- **Function:** Thread-safe access to shared memory across concurrent sessions

### Thompson Router
- **Path:** `shared/thompson_router.py`
- **Model labels:** Haiku, Sonnet, Opus. Unsupported non-Claude labels fail closed; CE does not map them to Sonnet.
- **Algorithm:** Bayesian inference with Beta distributions
- **Role:** Learns task outcomes and tuning signals; it no longer selects among separate API providers.

### Autonomous Learning
- **Path:** `learning/autonomous_learning.py`, `tools/autonomous-learner.py`
- **Workers:** 4 independent models capture outcomes, score, update priors, tune capability matrix
- **Trigger:** After every real task (online learning)
- **Storage:** `learning/autonomous_outcomes/`, `learning/autonomous_priors/`

### Portable Learning Artifacts
- **Path:** `learning/portable_artifacts.py`
- **Contract:** versioned `LearningArtifact` schema (`learning-artifact` `0.1`)
- **Format:** provider/runtime-neutral JSON, persisted as append-only JSONL
- **Purpose:** export reusable teaching signals, outcomes, and other learned state without CE execution objects, credentials, private reasoning, or source payloads
- **Portability:** artifacts can be serialized/deserialized independently of CE and are intended as candidates for promotion into Loom
- **Boundary:** deliberately no model registry, embedding store, orchestration framework, or learning platform

### Arbitration Orchestrator
- **Path:** `arbitration/`
- **Guarantees:**
  - No model is both arbiter and worker in same run
  - Each stage runs multiple independent workers followed by one arbiter
  - Workers and the arbiter are isolated within each stage
  - Each later stage receives complete prior-stage execution/review state
  - Stage count is configuration-driven; confidence does not terminate the pipeline
- **Context Access:** Git diffs, full files, dependencies, module context

### GA Tuning
- **Path:** `ga_tuning/`
- **Frequency:** Every 4 hours (0, 4, 8, 12, 16, 20 UTC)
- **Evaluators:** 5 independent (compression, Thompson, caching, capability matrix, learning rate)
- **Cost:** Zero (synthetic tasks, no API calls)
- **Parameters:** Scheduled installed runs log to `~/.FlossWare/claude-ensemble/shared/data/ga-tuning/parameter_evolution.md`; direct development runs use `ga_tuning/parameter_evolution.md`

### Model Discovery
- **Path:** `tools/discover-models.py`
- **Frequency:** The initialization script checks at most daily; it runs an inventory-only script
- **Scope:** Lists supported Claude Code aliases (haiku, sonnet, opus); does not probe Anthropic, Google, or Cursor APIs
- **Updates:** Model discovery does not obtain live provider metadata or update Thompson performance

---

## Dashboards

All dashboards read from local JSON files (no database):

| Dashboard | Command | Shows |
|-----------|---------|-------|
| Cost | `cost-dashboard.py` | Spending by model, provider, workflow, day |
| Thompson | `thompson-dashboard.py` | Routing accuracy, model rankings, quality achieved |
| Autonomous Learning | `autonomous-learning-dashboard.py` | Learning outcomes, prior updates, latest feedback |
| GA Tuning | `ga-tuning-dashboard.py` | Fitness progression, parameter evolution per evaluator |
| Performance | `performance_dashboard.py` | Real-time metrics (overlaps with cost dashboard) |

---

## Directory Structure

```
scripts/
  ensemble-init.sh            # Session initialization
  config.sh                   # Interactive tool configuration
  ga-tuning-schedule.sh       # Cron: GA every 4 hours

memory-service/
  memory_service.py           # Systemd daemon
  memory_client.py            # Session client
  rh-memory.service            # Systemd unit file
  install.sh                  # Install script

arbitration/
  orchestrator.py             # Multi-phase runner, context manager
  api_client.py               # Multi-model API client

learning/
  autonomous_learning.py      # Full system (4 workers)
  portable_artifacts.py       # Versioned portable learning artifact contract
  portable_artifacts.jsonl    # Default append-only artifact store
  autonomous_outcomes/        # Task outcome records
  autonomous_priors/          # Bayesian prior updates

shared/
  thompson_router.py          # Model selection

tools/
  arbitrate.py                # CLI tool
  autonomous-learner.py       # Wrapper for autonomous learning
  code-pr-review.js           # Code review skill
  code-doc.js                 # Documentation skill
  code-release-notes.js       # Release notes skill
  cost-dashboard.py           # Cost viewer
  discover-models.py           # Model discovery
  thompson-dashboard.py       # Thompson performance viewer
  autonomous-learning-dashboard.py  # Learning progress viewer
  ga-tuning-dashboard.py      # GA progress viewer

ga_tuning/
  ga_tuner.py                 # GA optimizer
  extract_and_apply_parameters.py  # Extract & update settings
  evaluators/                 # 5 independent evaluators
  parameter_evolution.md      # Direct development-run parameter history
  results/                    # Direct development-run output (JSON); installed output lives under shared/data/ga-tuning/results

compression/
  compression_api.py          # 64.6% reduction
  summarizer.py               # Text compression

caching/
  memory_cache_integration.py # Cache manager
  cache_metrics.py            # Hit/miss tracking

cost_tracking/
  logger.py                   # JSONL cost log
  aggregator.py               # Cost aggregation

hooks/
  memory-search-on-prompt.js  # TF-IDF + RRF semantic search

memory/
  MEMORY.md                   # Index (loaded at session start)
  feedback_*.md               # User preferences
  project_*.md                # Project context
```

---

## Portable / Standalone Mode

Claude Ensemble does not require the Red Hat workstation layout. Runtime paths can be
configured with environment variables:

| Variable | Purpose | Default |
|---|---|---|
| `ENSEMBLE_ROOT` | Repository root used by shell initialization | Repository containing `scripts/ensemble-init.sh` |
| `ENSEMBLE_CREDENTIALS_FILE` | Optional credentials file | `~/.FlossWare/secrets.env` |
| `ENSEMBLE_MEMORY_DIR` | Persistent CE memory directory | `~/.FlossWare/claude-ensemble-state/data/memory` for source-checkout installs |
| `ENSEMBLE_LOG_DIR` | CE service log directory | `~/.FlossWare/claude-ensemble-state/logs` for source-checkout installs |
| `ENSEMBLE_RUNTIME_DIR` | Runtime directory for configurable service sockets | XDG runtime/cache location |
| `ENSEMBLE_REPO_ROOT` | Repository/release root used by CE services | Current working directory |
| `ENSEMBLE_LEARNING_DIR` | Learning state directory | `~/.FlossWare/claude-ensemble-state/data/learning` for source-checkout installs |
| `ENSEMBLE_*_SOCKET` | Per-service Unix socket override | Existing service socket |

Both versioned and source-checkout installers manage the gateway and four core services. The memory client degrades gracefully when the daemon is unavailable. No Red Hat GitLab host, credential file, or repository path is required by the core runtime.

Example:

```bash
export ENSEMBLE_ROOT="$HOME/src/claude-ensemble"
export ENSEMBLE_MEMORY_DIR="$HOME/.local/share/claude-ensemble/memory"
export ENSEMBLE_LOG_DIR="$HOME/.local/state/claude-ensemble"
export ENSEMBLE_REPO_ROOT="$HOME/src/my-project"
```

On systems without systemd, use the CLI tools and standalone components directly rather
than installing the optional user services.

---

## Configuration

**Settings:** `settings.json.default` (copy to the selected CE profile's `settings.json`; do not edit ordinary `~/.claude/settings.json` for CE)

**Model authentication:** The active CE runtime invokes the installed Claude Code runtime, which owns model authentication. CE does not use `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, or `CURSOR_API_KEY` to route model requests in the supported single-runtime path.

**MCP credentials:** Configure credentials for each MCP server using its documented secure mechanism. Do not commit credentials or synchronize them through Git.

**MCP Servers:** configure the selected CE profile's `.mcp.json` as needed

---

## Cost Optimization Stack

Three complementary techniques:

1. **Thompson observations** — Record outcomes and estimated costs; they do not by themselves prove savings.
2. **Compression** — Measure input/output size for the workload being evaluated.
3. **Caching** — Report actual hits separately from misses and repeated inputs.

No fixed or combined savings percentage is claimed. Savings require workload-specific measurements, a defined baseline, and reproducible evidence.

---

## Learning Experiment Boundary

Claude Ensemble is also the lightweight experimental laboratory for learning and orchestration techniques. Experiments should remain small, dependency-free, isolated, measurable, and removable. Successful artifacts and techniques can be promoted into Loom; failed experiments are discarded rather than becoming permanent architecture.

Portable learning artifacts are the interchange boundary for that process:

```text
CE experiment
    ↓
portable learning artifact
    ↓
measure / validate
    ↓
share or retain
    ↓
Loom candidate
    ↓
promote or discard
```

The artifact layer intentionally excludes private prompts, credentials, private reasoning, and source payloads unless a future contract explicitly authorizes them.

## Future Work

See GitHub issues for the next learning experiments and capabilities. The portable artifact foundation is complete; outcome feedback (#176) is the next direct extension.

---

## Documentation

**Core Guides:**
- **`CLAUDE.ENSEMBLE.md`** — Best practices and coding standards
- **`MEMORY_SYSTEM.md`** — Memory persistence, search, analysis (NEW)
- **`REVIEW_SHORTHAND.md`** — Quick commands for code reviews (NEW)
- **[Workflow Learning Contract](docs/WORKFLOW_LEARNING_CONTRACT.md)** — Evidence requirements and Thompson update boundary
- **`MODEL_REGISTRY.md`** — Available models and capabilities

**Component Docs:**
- **`SERVICES_GUIDE.md`** — Systemd services (memory, thompson, learning, graph, and gateway)
- **`TOOLS_INTEGRATION_GUIDE.md`** — Thompson router, GA tuning, learning system
- **`cost_tracking/`** — Cost logging and aggregation
- **`ga_tuning/`** — Genetic algorithm parameter optimization
- **`learning/`** — Autonomous learning system details
- **Individual `README.md` in each service directory**

---

**Last updated:** 2026-10-04  
**Status:** Development and release acceptance in progress. Host deployment acceptance remains pending; see [issue #505](https://github.com/FlossWare/claude-ensemble/issues/505).  
**License:** See `LICENSE`  
**Contributors:** Generated with Claude Ensemble

## Operational Metrics API

The metrics REST listing is bounded and paginated. See [Operational Metrics REST API](docs/OPERATIONAL_METRICS.md) for page limits, continuation semantics, and aggregation behavior.


## Service Diagnostics

Run diagnostics from the repository checkout:

```bash
tools/ce doctor
tools/ce doctor --verbose
tools/ce doctor --json
tools/ce diagnose
tools/ce diagnose --deep
tools/ce diagnose --pipeline-test
tools/ce diagnose --service learning
tools/ce diagnose --json
```

`doctor` checks the local Claude Code integration and reports actionable configuration, hook-registration, systemd, and REST health findings. `diagnose` uses a shared correlation ID across bounded service probes and checks gateway readiness, including Graph and Memory. Deep mode adds read-only Memory/Graph/Decision checks and an isolated Learning-to-Thompson integration proof using temporary state and sockets. The diagnostic path does not write persistent Memory/Graph data, send alerts, or invoke a model. The pipeline test intentionally exercises isolated temporary state; it is not part of ordinary read-only diagnosis.


## Uninstalling a standalone deployment

Use the repository's `uninstall.sh` entry point to stop CE's five managed user services, remove only CE's managed unit files, and remove the active-release pointer. Set `SOURCE` to the source checkout you used for deployment, or to the selected immutable release before uninstalling:

```bash
SOURCE="$HOME/.FlossWare/ce-source"
bash "$SOURCE/uninstall.sh"
```

The default uninstall **preserves** immutable releases and all data under `shared/`, including Memory, Learning, logs, and the managed Claude Code profile. This permits later inspection or reactivation without destroying user state. Because the source checkout remains available, the same command can be used for explicit purge after the active pointer has been removed:

```bash
bash "$SOURCE/uninstall.sh" --purge
```

Alternatively, capture the selected release path before uninstalling and run its `uninstall.sh` twice. Do not use `--purge` if any Memory, Learning, logs, configuration, or other shared state must be retained. Uninstall refuses to proceed while an interrupted activation journal exists or if the active-release pointer is present but invalid. Resolve/recover the deployment first. The command removes only the five current CE unit files and retires the two legacy Alert/Messenger units; it does not edit ordinary `~/.claude`, shell startup files, the host crontab, or unrelated systemd units.
