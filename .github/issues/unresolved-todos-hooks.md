# Audit finding: unresolved workflow hook TODOs

**Tracking:** [GitHub issue #527](https://github.com/FlossWare/claude-ensemble/issues/527)  
**Status:** Partially resolved; external schema evidence still required.

## Resolution completed
- Marked `hooks/workflow-completion-hook.js` as a deprecated legacy path, not registered in `settings.json.default`.
- Documented the supported outcome path: `shared/learning-outcome-bridge.js` -> `tools/record_workflow_outcome.py` -> Learning service.
- Clarified that the supported evidence-backed outcome contract does not require semantic embeddings.
- Removed the misleading embedding-generation TODO from the legacy hook and added a regression test preventing it from being presented as the supported path.

## Remaining limitation
The legacy hook writes directly to `workflows.learnings`, but the table schema/migrations and complete set of consumers are not defined in this repository. We cannot honestly certify the external column nullability or consumer contract from repository evidence alone. Do not reactivate or extend that path until the owning database schema and consumers are available for review.

## Source
Audit recorded in commit [3462918](https://github.com/FlossWare/claude-ensemble/commit/34629180fd9d66e13c546175f9c6eece4ddd20c8).
