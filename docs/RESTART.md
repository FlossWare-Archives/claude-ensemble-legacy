# Claude Ensemble: clean restart

Status: planning baseline. No runtime architecture is approved by this document.

## Intent

Replace the existing implementation rather than continue a behavior-preserving cleanup.
The old implementation is preserved on branch `archive/pre-restart-2026-10-24`.

The archived implementation is evidence, not a specification. Do not carry over a
route, class, process boundary, storage representation, or algorithm merely because
it already exists.

## Working rules

1. Inventory required functionality before choosing implementation structure.
2. Distinguish services, in-process components, adapters, and algorithms.
3. Every service boundary must have an explicit ownership and operational justification.
4. Define contracts before implementations. Contracts should support composable
   implementations and decorators where they add value.
5. Keep algorithm choices such as BM25, tokenization, and cosine similarity behind
   capabilities; do not create a service for each algorithm.
6. Assign one clear owner to each canonical record and index. Define identity,
   revision, provenance, consistency, readiness, and recovery explicitly.
7. Treat REST as the service interface where a capability is a service; add other
   adapters only when there is a concrete consumer or requirement.
8. Specify failure semantics, errors, retryability, cancellation, deadlines, and
   observability as part of each relevant contract.
9. Use acceptance tests to express required behavior, not accidental legacy behavior.
10. Do not implement runtime services until the inventory, boundaries, and contracts
    have been reviewed.

## Functional inventory to resolve

This is an initial checklist, not a final service decomposition.

- Gateway and request orchestration
- Memory lifecycle and episodic records
- Durable Knowledge lifecycle, scope, provenance, and source ownership
- Graph entities, relationships, queries, and traversal
- Ingestion, reconciliation, chunking, and document revision handling
- Index ownership, corpus metadata/statistics, and recovery
- Embedding generation, model identity, and degraded behavior
- Exact-identifier, lexical, and vector candidate retrieval
- Ranking, fusion, deduplication, and stable candidate identity
- Prompt context assembly, filtering, and budget enforcement
- Thompson selection/statistics
- Learning and evidence-backed outcome capture
- Persistence backends and migration/recovery
- Installation, configuration, readiness, diagnostics, and operational lifecycle
- REST contracts and any justified MCP or other adapters

For every item, determine:
- user-visible purpose and consumers
- canonical owner and data ownership
- request/response contract and versioning
- state and persistence requirements
- error, retry, cancellation, and deadline behavior
- security and scope enforcement
- performance/availability expectations
- acceptance tests and operational diagnostics
- service, component, adapter, or algorithm designation, with rationale

## Restart sequence

1. Complete and review the functional inventory.
2. Agree on service versus component boundaries and their ownership.
3. Define language-neutral contracts, including decorator/composition expectations.
4. Define canonical data models, identity, provenance, persistence, and recovery.
5. Define acceptance criteria and test strategy.
6. Implement one vertical slice from contract through persistence and operations.
7. Add subsequent capabilities only against the agreed contracts.

## Archive and outstanding work

- Archived source baseline: `archive/pre-restart-2026-10-24`
- Existing PRs/issues from the old implementation are historical references unless
  explicitly re-scoped against this restart.
- Do not merge the old retrieval-validation or service-map PRs into the new baseline.
