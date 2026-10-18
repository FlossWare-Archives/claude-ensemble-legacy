# Audit finding: workflow completion hook embedding contract

**Tracking:** [GitHub issue #527](https://github.com/FlossWare/claude-ensemble/issues/527)  
**Status:** Open  
**Priority:** Investigate contract before implementation.

## Description
In `hooks/workflow-completion-hook.js`, a learning record is currently created with `embedding: null` and a TODO to generate an embedding. The intended ownership and requiredness of embedding generation have not yet been established.

## Triage guidance
Inspect the schema, write path, and semantic-search consumers before changing behavior. Confirm whether embeddings are optional, generated asynchronously, or required from this hook. Avoid duplicate generation and new provider dependencies without an established contract.

See [issue #527](https://github.com/FlossWare/claude-ensemble/issues/527) for acceptance criteria.
