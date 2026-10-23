# Claude Code source ingestion

Claude Ensemble can synchronize the local Claude Code CLI's Markdown instruction and auto-memory sources into its shared source archive and Memory Knowledge index. Claude.ai web/desktop memory is not in scope.

## Sources

The synchronizer discovers:

- Global `~/.claude/CLAUDE.md` and Markdown files under `~/.claude/rules/`.
- Project and nested `CLAUDE.md` and `AGENTS.md` files.
- Project-local `.claude/rules/**/*.md` files.
- Auto-memory Markdown under `~/.claude/projects/<project-key>/memory/`, but only when the project key matches an explicitly supplied `--project-root`.

Unknown auto-memory project keys are reported and excluded. Do not guess a mapping: incorrect project attribution can leak context between repositories.

## Dry-run first

From the repository root, inventory sources without changing any files or calling Memory ingestion:

```bash
python tools/claude-code-source-sync.py --project-root "$PWD"
```

Supply every project whose auto-memory should be mapped by repeating `--project-root`:

```bash
python tools/claude-code-source-sync.py \
  --project-root "$HOME/Development/github/FlossWare/claude-ensemble" \
  --project-root "$HOME/Development/github/FlossWare/loom-ai"
```

The JSON report lists source paths, kinds, scope, hashes, modification times, archive destinations, and unmapped auto-memory directories. It does not print Markdown contents.

## Apply

After reviewing the dry-run inventory, use the same roots with `--apply`:

```bash
python tools/claude-code-source-sync.py \
  --project-root "$PWD" \
  --apply
```

The source archive defaults to:

`~/.FlossWare/claude-ensemble/shared/data/memory-sources/`

Each source gets a stable path identity, content-addressed revision files, revision metadata, and a manifest. Archive directories use mode `0700`; archived files use `0600`. Original Claude Code files are never modified, moved, or deleted. Old content-addressed revisions are retained.

The synchronizer sends Markdown to the Memory service's `/memory/ingest` endpoint and reconciles stale sources only for scopes whose inventory and ingestion completed successfully. If an import fails, that scope is not reconciled, preserving its last known-good searchable records. Re-running the command retries failed ingestion and catches edits made while CE was stopped.

## Storage-path guard

The expected derived-index directory defaults to:

`~/.FlossWare/claude-ensemble/shared/data/memory/`

The synchronizer sends this expected path with each ingest request. Memory rejects the request with HTTP 409 if its configured storage directory differs. This is intentional: `shared/runtime_config.py` has legacy defaults under `~/.FlossWare/claude-ensemble-state/`, so the installed service's effective environment must be verified and reconciled before applying imports. Do not work around the guard by changing the expected path merely to make the import pass.

## Current limits

- Synchronization is repeatable but currently invoked manually; a watcher or scheduled reconciliation is not yet installed.
- The original Markdown is preserved and indexed by Memory's heading/paragraph chunker. Section-level semantic extraction into separately typed facts, preferences, decisions, and procedures is follow-up work; this initial importer does not claim to have completed that extraction.
- Session transcript import is tracked separately in [issue #677](https://github.com/FlossWare/claude-ensemble/issues/677).
- A successful import on the target host has not been claimed until the dry-run, service-path guard, reindex, and real Claude Code recall acceptance have passed.
