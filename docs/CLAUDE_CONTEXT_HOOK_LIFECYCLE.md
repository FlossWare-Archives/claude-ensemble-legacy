# Claude Code Context Hook Lifecycle

Issue #311 establishes one canonical prompt-time context retrieval path.

## Contracts

Claude Ensemble keeps two concepts distinct even when they share the Memory service:

- **Memory**: episodic experience and observations, meaning what happened.
- **Knowledge**: durable Claude Code material that represents what is currently known or believed.

The `UserPromptSubmit` hook exposes these as separate sections in its `additionalContext` payload. Claude Code does not need to know how the service stores either category.

## Retrieval lifecycle

1. Claude Code submits a user prompt.
2. `~/.claude/hooks/memory-search-on-prompt.js` receives the event.
3. The complete prompt is used as the retrieval query. Retrieval is relevance-driven and does not depend on trigger words such as `remember`, `recall`, or `prior`.
4. The hook calls the loopback Memory REST endpoint `POST /memory/search`.
5. Results are validated against the canonical `{ok: true, results: []}` envelope and require a string `content` field.
6. Results are deduplicated by source, file, section, and content.
7. Results are partitioned into Memory and Knowledge. Claude Code-ingested documents (`source=claude-code` or `claude-code-*` documents) are treated as Knowledge; other Memory service search results remain Memory.
8. The hook emits `UserPromptSubmit.hookSpecificOutput.additionalContext`.
9. Memory failures remain fail-open and never block prompt submission.

## Legacy hooks

`hooks/user-prompt-submit.sh` and `hooks/ingest-prompt` are compatibility shims only. They no longer perform retrieval in the repository. The installer also recognizes the known deployed legacy implementations by their exact content hashes and removes only those owned registrations during upgrade. The legacy files themselves are left in place unless the user removes them. Arbitrary same-named or modified scripts are not migrated.

Workflow-specific context loaders remain available for workflow orchestration. They are not additional Claude Code prompt hooks and should not register a second `UserPromptSubmit` retrieval path.

## Capture versus retrieval

Session-end learning and workflow-completion hooks capture experience. They must not also retrieve prompt context.

The lifecycle therefore separates:

- **Retrieve**: prompt-time context loading.
- **Capture**: session/workflow experience persistence.
- **Learning**: extraction and evaluation of captured experience.
- **Promotion**: durable knowledge creation or update from validated learning.

Promotion is intentionally not implemented by the prompt hook. The hook is a read-only context augmentation path.

## Duplicate prevention

The canonical retrieval path deduplicates identical returned context items. Legacy prompt retrieval hooks are disabled as compatibility shims so an older Claude Code registration cannot perform a second retrieval.

Future cross-hook deduplication should use the query hash exposed in the hook metadata rather than introducing another retrieval implementation.


## Event ownership and write boundaries

| Event/component | May read | May write | Must not do |
| --- | --- | --- | --- |
| `UserPromptSubmit` canonical context hook | Memory and Knowledge through Memory REST search | Nothing; it only returns context | Capture events, invoke learners, or promote Knowledge |
| `SessionStart` context loader | Local, already-approved context index | Nothing | Trigger learning or update learner state |
| `PostToolUse` capture adapter | The event payload needed for capture | An idempotent capture request to Memory | Update Thompson/GA state or promote Knowledge |
| `SessionEnd` capture adapter | Session outcome and explicitly supplied observations | An idempotent capture request to Memory | Replay all historical outcomes as new events |
| Workflow-completion adapter | The completed workflow result and stable run ID | One operational outcome/learning artifact through the Learning service | Directly mutate Thompson/GA state or infer correctness from completion alone |
| Learning service / scheduled learner | Persisted outcome artifacts plus evidence and provenance | Learner state and learning artifacts | Treat telemetry, model consensus, or successful completion as ground truth |
| Knowledge promotion | Validated learning artifacts and cited evidence | Explicit Knowledge additions/updates | Promote raw hook output or unreviewed suggestions automatically |

### Idempotency and retry contract

- Every captured event must carry a stable event/run identifier supplied by the originating workflow. Wall-clock timestamps are metadata, not idempotency keys.
- Retrying the same event must update/reconcile the existing capture or be rejected as a duplicate; it must not append another learning outcome or apply a learner update twice.
- Capture failure is fail-open for Claude Code execution, but it must be visible through structured diagnostics. A hook must not report a successful durable write when the service has not acknowledged it.
- Learning-service retries must be idempotent by outcome ID. A learner update must record the outcome/evidence IDs it consumed so a retry cannot apply the same evidence twice.
- Missing or invalid outcome evidence means “capture only”; it must never be converted to a synthetic positive or negative learning signal.
- Knowledge promotion is a separate explicit operation. Promotion candidates must retain provenance to the outcome and evidence that justify them.

### Known unsafe legacy path

`hooks/post-task-analysis.js` previously declared `UserPromptSubmit` as its event while describing post-completion analysis, and directly launched `learning/post_task_analyzer.py`, which updates Thompson priors. That couples prompt submission to learning and bypasses the Learning-service boundary. The adapter is now disabled and must remain disabled until it delegates through the canonical Learning service with stable outcome IDs and evidence-gated updates.

The older session-end capture scripts also have no stable per-event idempotency key and one script summarizes the entire historical autonomous-outcome directory on every session. They are legacy capture paths, not proof of exactly-once processing. Do not register both session-end scripts for the same event; replace them with one service-backed, idempotent capture adapter before relying on their output for learning.


### Existing installation warning

The disabled source files do not automatically disable copies already installed in a user's `~/.claude` hooks directory or referenced by existing Claude Code settings. During rollout, inspect each installation's hook registrations and replace or unregister any copied `post-task-analysis.js` or `post-workflow-learning.js` adapter. Do not assume updating the repository alone updates deployed copies. The installer should eventually perform ownership-aware migration and verification for these legacy adapters.


## Implemented SessionEnd capture

The canonical `hooks/session-end-memory-capture.js` adapter is registered for Claude Code's `SessionEnd` event by both the top-level `install.sh` installer and the Claude Config installer. Both use the ownership-aware JSON settings tool and preserve unrelated user hooks. It sends a deterministic event to `POST /memory/append-once` in the `session_events` stream. The idempotency key is derived from the stable Claude Code `session_id`; the payload contains only the event type, session ID, event name, and source. It deliberately does not read or upload the transcript, transcript path, working directory, or conversation contents.

The adapter accepts only a confirmed `stored` or `duplicate` acknowledgement as success. Missing session IDs and service failures produce stderr diagnostics and exit successfully so session shutdown remains fail-open. It does not trigger Learning, mutate learner state, or promote Knowledge.

Installer migration unregisters only known legacy SessionEnd scripts whose deployed file content matches an allowlisted SHA-256. It leaves the script files in place and preserves modified or unrecognized hooks. Both managed hooks are checksum-tracked, backed up, and rolled back with the settings file as one installation transaction.
