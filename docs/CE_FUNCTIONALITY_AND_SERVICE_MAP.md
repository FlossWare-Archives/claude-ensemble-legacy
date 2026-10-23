# CE Functionality and Service Map

Status: working architecture inventory, not an ADR and not an implementation commitment.

Scope: Claude Ensemble (CE) only. This document maps CE's current responsibilities and proposes boundaries for review before runtime changes.

## Boundary vocabulary

- **Service**: owns a cohesive domain or operational responsibility and exposes a stable contract to other CE components.
- **Capability**: a named operation with an explicit contract. It may initially be implemented in-process or inside an existing service.
- **Implementation strategy**: a replaceable algorithm or mechanism behind a capability contract.
- **Adapter/orchestrator**: connects existing contracts or translates between an external runtime and CE; it should not become the owner of the underlying domain logic.

A capability may warrant a service boundary even if its implementation is small. Service extraction should be based on ownership, independent consumers, lifecycle/resource isolation, consistency needs, and operational value, not source-file size.

## Existing managed runtime

The current `SERVICES_GUIDE.md` defines the supported runtime as the gateway plus four application services.

| Existing service | Current responsibility | Boundary notes |
|---|---|---|
| Gateway | Client-facing REST entry point, request routing, orchestration and integration | Keep it as the public entry point. Avoid moving domain ownership into it. |
| Memory | Episodic memory operations; also currently owns durable Knowledge retrieval and hybrid indexing | Responsibilities are broader than the service name suggests. Review domain and retrieval boundaries. |
| Graph | Durable nodes/edges, graph query and traversal | Own graph data and graph operations. Retrieval consumers should call its contract rather than reimplement graph traversal. |
| Thompson | Model/workflow selection statistics and recommendations | Selection policy is distinct from document relevance ranking. Do not conflate these services. |
| Learning | Evidence-backed outcome capture and learner updates | Own outcome recording and learning workflows; integrate with Thompson through an explicit contract. |

Alert and Messenger source directories may remain in the repository, but the service guide says they are not part of the supported active runtime.

## Current retrieval implementation findings

The current code places several concerns inside `memory-service/hybrid_retrieval.py`:

- Markdown chunking and heading/line provenance
- Tokenization and term-frequency generation
- Persistent SQLite chunk storage and document revision signatures
- Embedding model loading and vector generation
- Corpus statistics used for BM25-style scoring
- Lexical scoring, semantic similarity scoring, fixed-weight hybrid combination, thresholds and final sorting
- Cache warm-up, invalidation, reindexing and embedding backfill support

SQLite is embedded storage, not a separate network service.

`memory-service/memory_service.py` also maintains a per-file in-memory chunk/search cache and contains memory operations, ingestion reconciliation, Knowledge scope checks, exact-identifier handling, and HTTP/Unix-socket request handling. This means indexing, retrieval, data ownership, and protocol adaptation currently overlap within the Memory implementation.

The Memory HTTP API exposes overlapping routes including `/memory/search`, `/memory/search-semantic`, and `/memory/search-hybrid`. The hook currently calls `/memory/search`. Before any extraction, determine which operations are canonical, which are aliases, and which older paths remain supported intentionally.

The repository also has standalone chunking/search utilities and older retrieval prototypes. Their presence alone does not make them part of the supported runtime contract. Confirm callers and tests before classifying any path as active or legacy.

## Proposed capability inventory

The table distinguishes logical responsibility from whether it should run as its own process.

| Responsibility | Initial designation | Service-boundary assessment |
|---|---|---|
| Memory record lifecycle and episodic storage | Memory service | Existing service; retain ownership of memory writes, reads, append semantics, and record identity. |
| Durable Knowledge lifecycle, provenance, status, and source scope | Knowledge capability / service candidate | A distinct service is justified if Knowledge needs independently owned records, lifecycle, access policy, or ingestion state. Do not split only to rename existing routes. |
| Corpus/index ownership | Index capability / strong service candidate | Own canonical indexed document/chunk revisions, index status, corpus metadata/statistics, and consistency/recovery semantics shared by retrieval strategies. |
| Source discovery, ingestion planning, reconciliation, and archival | Ingestion capability; possible service | Keep explicit dry-run/apply semantics and source ownership. Separate runtime is justified if independent scheduling, workload, or recovery needs require it. |
| Chunking and section/line provenance | Versioned capability | Contract should specify boundaries, overlap, metadata, identity/revision behavior, and deterministic output. Separate service not yet required. |
| Tokenization and term normalization | Index/query capability | One consistent contract must be used for both indexing and query processing. |
| Embedding generation and model identity | Embedding capability / conditional service candidate | Define model identifier, dimensions, normalization, and failure behavior. Separate service if multiple consumers need shared model lifecycle, resource isolation, or independent scaling. |
| Exact identifier lookup | Retrieval capability | Preserve boundary-aware exact matching; do not force machine identifiers through approximate semantic ranking. |
| Lexical candidate retrieval | Retrieval capability | Produces candidates and lexical evidence. BM25 is a strategy, not a standalone domain service. |
| Vector candidate retrieval | Retrieval capability | Produces semantic candidates and similarity evidence against the same canonical corpus/index. |
| Corpus statistics | Index service responsibility | Statistics must be scoped and consistent with the corpus used by the query. Do not let lexical and vector paths maintain conflicting corpus definitions. |
| Candidate ranking and fusion | Ranking capability / strong service candidate | Shared policy should accept candidates and evidence from multiple retrieval methods, return stable ordering, and identify the applied policy. |
| Reranking | Ranking capability; conditional separate service | Keep behind a strategy contract. Separate runtime may be justified by model size, latency, accelerator/resource needs, or independent lifecycle. |
| Metadata and project-scope filtering | Retrieval/index capability | Scope enforcement must be consistent and must happen before results are exposed. |
| Result identity and deduplication | Retrieval/ranking capability | Define stable candidate identity across lexical, vector, Memory, Knowledge, and Graph sources. |
| Provenance/evidence references | Shared capability with domain ownership | Each domain remains responsible for the truth of its records; shared primitives can standardize source references and stable IDs. |
| Prompt-context assembly and budget enforcement | Hook/orchestration capability | Combines bounded Memory, Knowledge, and Graph results and preserves source distinctions. It should not own retrieval algorithms. |
| Claude Code hook integration | Adapter | Converts CE results into the supported `UserPromptSubmit` context contract. It should remain fail-open only where that behavior is explicitly intended and observable. |
| Thompson model/workflow selection | Thompson service | Separate from ranking retrieved documents. |
| Outcome capture and learner updates | Learning service | Keep outcome evidence and feedback contracts explicit. |
| Graph traversal and relationship queries | Graph service | Graph owns relationship semantics; retrieval may consume graph-derived candidates as one source. |

## Candidate target architecture

The initial target should have explicit contracts for:

1. **Index**: canonical document/chunk revisions, corpus statistics, index status, upsert/remove, and maintenance/recovery.
2. **Retrieval**: exact, lexical, and vector candidate retrieval with consistent scope filters and source evidence.
3. **Ranking**: candidate fusion, relevance policy, deterministic tie-breaking, optional reranking, and score/policy metadata.
4. **Memory**: episodic record lifecycle and retrieval domain semantics.
5. **Knowledge**: durable knowledge lifecycle and provenance, potentially a separate service after domain ownership is confirmed.
6. **Graph**: relationship storage/query and graph traversal.
7. **Embedding**: stable model/vector contract; keep in-process until shared consumers or resource/lifecycle needs justify a service.
8. **Ingestion**: explicit source inventory, dry-run, apply, reconciliation, and recovery behavior.
9. **Prompt context**: bounded composition of results through the hook/orchestration layer.

These are logical responsibilities, not a commitment to nine separate processes. Index and Ranking are strong candidates for service boundaries because they centralize shared policy and state used by multiple retrieval paths. Knowledge needs a domain-boundary review. Embedding and Ingestion should become independent services only when operational requirements justify the cost.

## Decisions to settle before implementation

1. Establish the canonical search route and mark overlapping routes as canonical, aliases, or deprecated.
2. Trace all production callers and tests for current hybrid, legacy lexical, exact-identifier, and standalone chunking paths.
3. Decide whether Knowledge needs independent record ownership and lifecycle or remains a capability owned by Memory.
4. Define the Index contract and the single source of truth for chunks, revisions, statistics, and index readiness.
5. Define candidate/result identity and the Retrieval contract.
6. Define Ranking inputs, score semantics, fusion policy, deterministic ordering, and explanations.
7. Define chunking and embedding model/version compatibility contracts.
8. Only then decide which contracts need separate processes and managed endpoints.

## Guardrails

- Do not make BM25, cosine similarity, tokenization, score normalization, or every helper function its own service by default.
- Do not duplicate corpus statistics, index metadata, source-scope policy, or provenance logic across retrieval methods.
- Do not confuse Thompson's model/workflow selection with document relevance ranking.
- Do not introduce a new service until its owner, contract, consumers, failure semantics, and operational reason are explicit.
- Keep this work scoped to CE. This document is a working map, not an ADR and not authorization to split services before the boundaries are reviewed.
