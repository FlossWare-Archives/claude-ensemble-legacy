# Claude Ensemble

**Complete, production-ready multi-AI orchestration toolkit.**

Claude Ensemble is a portable toolkit for orchestrating multiple AI models and Claude Code workflows. It combines model routing, multi-phase arbitration, autonomous outcome learning, cost tracking, compression, caching, and reusable workflow skills.

It is designed to work both in Red Hat-centric environments and as a standalone personal or open-source toolkit. Runtime paths, credentials, service sockets, and repository locations are configurable rather than hard-coded.

---

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
- **Model Discovery** — Probes Anthropic/Google/Cursor APIs every 4 hours for latest models
- **Cost Tracking** — JSONL audit log of all API usage
- **5 Dashboards** — Cost, Thompson routing, autonomous learning, GA tuning, performance

### Workflow Skills (Learning-Integrated)
- **Code PR Review** (`code-pr-review`) — AI consensus code review, Thompson-routed model selection
- **Documentation** (`code-doc`) — Auto-generate docs, learns which models write better
- **Release Notes** (`code-release-notes`) — Generate from commits with multi-AI consensus
- **Autonomous Variants** (`code-pr-review-auto`, `code-doc-auto`) — Run without user confirmation

All skills feed outcomes into Thompson — models learn task-specific performance over time.

### Capabilities  
- **Compression** — 64.6% token reduction via recursive text compression
- **Caching** — Prompt caching framework with hit/miss tracking (Phase 1 ready)
- **Hooks** — Memory search on prompt submit, learning extraction
- **Memory** — Project memory files (feedback, projects, references)

---

## Quick Start

### Installation

```bash
curl -fsSL https://raw.githubusercontent.com/FlossWare/claude-ensemble/main/install.sh | bash
```

This will:
1. Clone the repository
2. Create symlinks in `~/.claude/`
3. Set up credentials storage at `~/.FlossWare/secrets.env`
4. Prompt you to configure which tools to enable

**See [CREDENTIALS_SETUP.md](CREDENTIALS_SETUP.md)** for how to configure API tokens and credentials (auto-loaded in all sessions).

### Session Initialization
On startup, `scripts/ensemble-init.sh` automatically:
1. Connects to memory service (systemd daemon)
2. Initializes autonomous learning
3. Discovers latest models (Claude 5, Gemini, Cursor)
4. Prints toolkit status

```bash
✓ Connected to memory service
✓ Autonomous learner ready
✓ Claude Ensemble Toolkit initialized
  Tools: compression, caching, cost_tracking, ga_tuning, thompson_router, arbitration
  Memory: ~/.claude/projects/memory
  Arbitration: multi-phase orchestrator for critical decisions
  Autonomous learning: Thompson continuously improving from real tasks
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

See **[SKILL_INTEGRATION_GUIDE.md](SKILL_INTEGRATION_GUIDE.md)** for full details on how skills learn and route models via Thompson.

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
- `/api/v1/models/*` remains the model inventory and invocation surface, including credential status.
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
- **Models:** Haiku (cheap), Sonnet (balanced), Opus (capable), Cursor, Gemini
- **Algorithm:** Bayesian inference with Beta distributions
- **Cost Savings:** 67% vs Opus-for-all (tested)

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
- **Parameters:** Tuned automatically, logged to `ga_tuning/parameter_evolution.md`

### Model Discovery
- **Path:** `tools/discover-models.py`
- **Frequency:** Every 4 hours (synced with GA tuning)
- **Providers:** Anthropic (free API call), Google (free API call), Cursor (defaults to latest)
- **Updates:** Thompson router inline, settings.json with discovery timestamp

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
  parameter_evolution.md      # Timestamped parameter changes
  results/                    # GA output (JSON)

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
| `ENSEMBLE_MEMORY_DIR` | Persistent memory directory | `~/.claude/projects/memory` |
| `ENSEMBLE_LOG_DIR` | Service log directory | `~/.claude` |
| `ENSEMBLE_RUNTIME_DIR` | Runtime directory for configurable service sockets | XDG runtime/cache location |
| `ENSEMBLE_REPO_ROOT` | Repository inspected by alerting | Current working directory |
| `ENSEMBLE_LEARNING_DIR` | Learning state directory | Existing Claude Ensemble learning path |
| `ENSEMBLE_ALERT_DIR` | Alert state directory | `~/.claude/alerts` |
| `ENSEMBLE_*_SOCKET` | Per-service Unix socket override | Existing service socket |

For a standalone installation, services are optional. The memory client already degrades
gracefully when its daemon is unavailable, and the learning/Thompson/alert services can
be run only when their corresponding features are needed. No Red Hat GitLab host,
credential file, or repository path is required by the core runtime.

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

**Settings:** `settings.json.default` (copy to `~/.claude/settings.json` to customize)

**Credentials:** `~/.FlossWare/secrets.env` or environment variables
- ANTHROPIC_API_KEY
- GOOGLE_API_KEY
- CURSOR_API_KEY
- Custom keys for any MCP servers you configure

**MCP Servers:** `~/.mcp.json` (configure as needed)

---

## Cost Optimization Stack

Three complementary techniques:

1. **Thompson Router** (67% savings) — Route to cheapest capable model
2. **Compression** (64.6% reduction) — Token reduction via text compression
3. **Caching** (69.8% savings) — Reuse frequent prompts

**Combined:** Can exceed 90% savings on token-heavy workflows.

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
- **`SKILL_INTEGRATION_GUIDE.md`** — How workflow skills work
- **`MODEL_REGISTRY.md`** — Available models and capabilities

**Component Docs:**
- **`SERVICES_GUIDE.md`** — Systemd services (memory, thompson, learning, alert, messenger, graph)
- **`TOOLS_INTEGRATION_GUIDE.md`** — Thompson router, GA tuning, learning system
- **`cost_tracking/`** — Cost logging and aggregation
- **`ga_tuning/`** — Genetic algorithm parameter optimization
- **`learning/`** — Autonomous learning system details
- **Individual `README.md` in each service directory**

---

**Last updated:** 2026-10-04  
**Status:** Production-ready, all tools active  
**License:** See `LICENSE`  
**Contributors:** Generated with Claude Ensemble
