# CE Functionality and Service Map

Status: working architecture inventory, not an ADR and not an implementation commitment.

Scope: Claude Ensemble (CE) only. This document maps CE's current responsibilities and proposes boundaries for review before runtime changes.

## Boundary vocabulary

- **Service**: owns a cohesive domain or operational responsibility and exposes a stable contract to other CE components.
- **Capability**: a named operation with an explicit contract. It may initially be implemented in-process or inside an existing service.
- **Implementation strategy**: a replaceable algorithm or mechanism behind a capability contract.
- **Adapter/orchestrator**: connects existing contracts or translates between an external runtime and CE; it should not become the owner of the underlying domain logic.

A capability may warrant a service boundary even if its implementation is small. Service extraction should be based on ownership, independent consumers, lifecycle/resource isolation, consistency needs, and operational value, not source-file size.

## Revised architectural principle: contract-first services

The target should be a complete inventory of CE service-level capabilities, with an explicit REST contract for each. Do not decide that a capability is “not a service initially” merely because its first implementation can run in-process.

For every service-level capability, document:
- its owned responsibility and domain invariants;
- a versioned request/response contract and REST endpoint;
- error taxonomy, validation, deadlines/timeouts, cancellation, retryability, and readiness expectations where applicable;
- data and provenance ownership;
- known consumers and authorization/scope rules;
- contract tests and compatibility requirements;
- implementation/deployment mode, recorded separately from the contract.

**A REST endpoint does not require a separate process.** A service may be hosted in the existing CE runtime initially and still expose the same REST contract. The contract must be callable independently; the implementation can use an in-process adapter where that is operationally appropriate. This preserves independent addressability and substitution without forcing every internal call through a network hop.

The inventory must distinguish service-level capabilities from implementation strategies. BM25, cosine similarity, tokenization, score normalization, and chunking algorithms are not automatically independent services simply because they are named or replaceable. They belong behind the appropriate service/capability contract unless CE has a separate domain responsibility and consumers that justify their own service API.

The architecture review must therefore answer two separate questions for each item:
1. **What is the contract/service boundary?** This is part of the target architecture inventory.
2. **Where does the implementation run?** This is a deployment decision and must not be used to erase or blur the contract boundary.

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


## Route and caller trace findings

A first caller trace confirms that the route names currently obscure the actual implementation:

- `hooks/memory-search-on-prompt.js` calls `POST /memory/search`; hook tests assert this route.
- The HTTP handler maps both `/memory/search` and `/memory/search-semantic` to the `search_semantic` operation. That operation uses the persistent `HybridRetrievalIndex.search()` for prose queries and a separate in-memory exact-identifier path for structured keys.
- `/memory/search-hybrid` takes a different, older path: it calls the legacy `MemoryStore.search()` keyword scorer, calls `search_semantic()`, and merges their results with a second fixed 0.4/0.6 weighting policy.
- Therefore `/memory/search` already reaches hybrid BM25/vector retrieval for prose. The name `/memory/search-hybrid` does not identify the canonical hybrid index; it invokes a second hybridization path built around the legacy keyword search.
- `MemoryStore.search()` rescans Markdown files to calculate document frequency, chunks documents, and computes a separate TF/IDF-like score. That is a second lexical ranking implementation alongside the BM25 scoring in `HybridRetrievalIndex.search()`.
- The current production hook contract and tests make `/memory/search` the compatibility-critical entry point. Do not switch the hook to `/memory/search-hybrid` merely because the latter sounds more accurate.

### Consequence

The first retrieval cleanup should be a behavior-preserving consolidation, not immediate service extraction:

1. Define one canonical retrieval operation and a versioned result contract.
2. Preserve exact-identifier behavior, Memory/Knowledge source filtering, project scoping, stable candidate identity, and provenance.
3. Establish regression coverage for hook callers and both current search paths.
4. Decide whether `/memory/search-semantic` remains an alias and whether `/memory/search-hybrid` is deprecated, redefined, or kept as a distinct explicit contract.
5. Only after behavior is pinned should the lexical scorer, index ownership, and ranking/fusion policy be separated behind capability interfaces.

This is the clearest initial refactoring seam: two lexical/ranking paths currently coexist, while the shared index and ranking responsibilities have not yet been made explicit contracts.


## Retrieval contract draft (based on current callers)

This section records a proposed logical contract for review. It does not change the HTTP API or imply that each operation must be a separate process.

### Canonical operation

Treat `POST /memory/search` as the canonical compatibility entry point because the prompt hook uses it. Keep `/memory/search-semantic` as a documented alias during migration unless caller inventory proves it can be removed. Do not preserve the name `/memory/search-hybrid` as if it were equivalent: its current implementation is a separate legacy fusion algorithm.

The canonical logical operation is **retrieve candidates under an explicit source and scope, then rank them under one policy**. The current implementation can continue to serve that contract while internal interfaces are introduced.

### Request semantics to preserve

| Field | Contract |
|---|---|
| `query` | Required non-empty string. Preserve internal separators for machine identifiers; only terminal prose punctuation may be normalized for identifier classification. |
| `limit` | Preferred result bound. `top_k` remains a compatibility alias; if both are supplied, `limit` wins, matching current REST handling. Validate the bound rather than silently coercing invalid values. |
| `source` | Optional enum: omitted (all eligible sources), `memory`, or `claude-code` (durable Knowledge documents). Apply this filter before candidate ranking, not after unrelated sources have consumed the result budget. |
| `project_root` | Optional Knowledge scope constraint. It must be applied when determining eligible Knowledge documents, before retrieval and ranking. It must not broaden scope when absent or invalid. |
| transport | HTTP and the existing private socket are adapters to the same domain operation; transport details must not fork retrieval semantics. |

### Result semantics to preserve and make explicit

A result should have a stable candidate identity and carry enough evidence for downstream composition without rereading unrelated sources. The proposed logical fields are:

- `id`: stable identity for the indexed chunk, not its rank or current score.
- `source`: `memory`, `claude-code`, or another explicitly supported source when added.
- `document_id` / `file`: stable document identity, retaining the current file field as a compatibility alias where needed.
- `section`, `heading_path`, and source location/provenance when available.
- `content`: the retrieved chunk content, not an unrelated whole-document payload unless a caller explicitly requests that representation.
- `scores`: separate evidence such as lexical, vector, exact-identifier, and optional graph scores. Scores from different strategies must not be assumed comparable without an explicit normalization/fusion policy.
- `ranking_policy` or equivalent metadata: identifies the policy/version that produced the order.
- `metadata`: source scope, document revision, chunker/index version, and embedding model identity where relevant.

This is a target logical shape, not a mandate to break the existing response JSON immediately. Existing hook consumers currently expect `file`, `section`, `content`, and `score`; migration should preserve those fields until callers are updated together.

### Error and readiness semantics

- A not-ready index must not return a misleading successful empty result. Preserve the current explicit 503 response with `error_code=index_not_ready`, `retryable`, and index status.
- Distinguish invalid request/source/scope input from internal indexing or model failures.
- Embedding model identity and vector dimension mismatches are consistency failures requiring a clear error and operator action, not silent score degradation.
- The hook may fail open at the Claude Code integration boundary, but service errors must remain observable in logs/diagnostics. Fail-open behavior must not be used to disguise a broken index as “no memories found.”
- Index writes and deletes need durable retry/reconciliation semantics. Readiness should reflect pending index work rather than only whether the process is alive.

### Exact identifiers are a retrieval mode, not just another score

Structured keys (UUIDs, run/event IDs, dotted artifact types) currently use boundary-aware matching and should not be diluted by semantic similarity or matched as prefixes/substrings of other identifiers. Keep this as an explicit candidate-retrieval strategy selected by query classification. The classification rule itself needs tests and a versioned contract because changing it can silently alter which path a query takes.

### Candidate service boundaries after this trace

1. **Index service: strong candidate.** It owns canonical document/chunk revisions, chunk identities, index readiness, corpus statistics, source metadata, and durable maintenance/recovery. It is the natural single authority for the data that lexical and vector retrieval both consume. SQLite may remain the initial implementation; a service boundary is a contract/ownership decision, not a requirement to replace SQLite with a database server.
2. **Ranking service: strong candidate, but only after the contract is stable.** It owns candidate fusion, score semantics, policy/version identity, deterministic tie-breaking, and optional reranking. It should accept candidates plus evidence and return ranked candidates; it should not own corpus/index state.
3. **Retrieval capability: required; separate service is not yet proven.** Exact, lexical, and vector candidate generation need explicit interfaces and shared scope enforcement. Whether this is hosted by Index or a distinct Retrieval service depends on independent consumers and operational requirements. Do not create a third daemon by reflex.
4. **Knowledge: currently a Memory-owned capability, not a proven independent service.** Current REST routes, file naming, ingestion ledger, and status operation distinguish durable Knowledge, but it is still stored and served through Memory. Split it only if the review establishes independently owned write/lifecycle/access-policy semantics, not merely because Memory's name is too narrow.
5. **Embedding: capability with a versioned model/vector contract.** A separate service is justified only if CE has multiple consumers that need shared model lifecycle, independent scaling, or resource isolation.
6. **Chunking and tokenization: versioned capabilities.** Their outputs and compatibility rules matter to the index, but the algorithms do not need independent network endpoints by default.

### Safe implementation order

1. Add/confirm contract tests for source filtering, project scope, exact identifier boundaries, limit precedence, deterministic ties, provenance, index-not-ready errors, and failed-index recovery.
2. Inventory all production callers and mark `/memory/search-hybrid` as legacy or retain it only with an explicit distinct contract. Do not silently change its result semantics.
3. Introduce internal candidate/result and retrieval interfaces behind the existing endpoint.
4. Route all supported search entry points through one ranking policy; keep the old fusion path only during a measured compatibility migration.
5. Extract index ownership behind an interface and verify restart/recovery and stale-revision behavior.
6. Extract Ranking as a managed service only after its contract and independent operational need are established.
7. Reassess Knowledge ownership against actual domain write and lifecycle rules.

No runtime code is changed by this document. In particular, this draft does not authorize immediate endpoint removal, result schema changes, or deployment of new daemons.


## Additional caller and test inventory

A broader repository-tree trace found additional retrieval-related artifacts that must be classified explicitly rather than assumed to be live service contracts.

| Artifact | Evidence / role | Initial classification |
|---|---|---|
| `hooks/memory-search-on-prompt.js` | Current hook calls `POST /memory/search`, requests Memory and Knowledge separately, and can query Graph for bounded relationships. | Active compatibility-critical hook. |
| `tools/claude-config/tests/test-memory-hook.mjs` | Verifies POST route, source-separated requests, configured limits, context output, and cross-query behavior. | Active regression suite for the current hook. |
| `hooks/test-memory-search-on-prompt.js` | Tests prompt decomposition and Knowledge retrieval through `/memory/search`. | Hook regression coverage; retain while this hook is installed/supported. |
| `memory-service/test_hybrid_retrieval.py` | Exercises the persistent SQLite index, warm-up, scoring, embedding, and corpus behavior. | Active unit-level coverage for the index implementation. |
| `memory-service/test_memory_service.py` | Covers REST search, source separation, exact identifiers, revisions, writes/deletes, readiness, and failure/recovery cases. | Active service/contract coverage. |
| `memory-service/test_knowledge_status.py` | Tests active indexed Knowledge document count and empty/available status. | Active Knowledge lifecycle/status coverage. |
| `hooks/memory-rag-search.js` | Older manual RAG helper with its own local memory directory and ChromaDB assumptions; it does not use the current Memory REST contract. | Legacy/manual tool until installation and callers prove otherwise. Do not treat it as a second canonical retrieval API. |
| `shared/semantic-search.py` | Standalone semantic-search utility outside the managed Memory service path. | Caller inventory required before reuse or removal; not evidence of a managed service. |
| `docs/MEMORY_RETRIEVAL.md` and `memory-service/test_memory_retrieval_followups.py` | Describe/test execution-aware JSONL retrieval with relationship priority (parent, ancestor, same-request, related-lineage). | Separate retrieval domain from Markdown RAG. Do not collapse these ranking semantics into document relevance ranking. |
| `knowledge-service/provenance.py` | Shared stable IDs and source-evidence primitives for corpus-derived Knowledge and Graph records. No service entry point or standalone service guide was found in this trace. | Shared capability/module, not proof that a Knowledge service currently exists. |

### Important distinction: two different retrieval domains

CE has at least two retrieval domains with different ranking semantics:

1. **Document RAG retrieval**: Markdown chunks, lexical/vector relevance, source/project scope, and prompt context. This is the domain where Index and Ranking boundaries are candidates.
2. **Execution-aware Memory retrieval**: JSONL records selected through execution context and relationship priority. Its parent/ancestor/same-request/related-lineage ordering is domain policy, not a generic document relevance score.

A future Ranking service must not absorb both domains into one undifferentiated “score.” It may provide shared mechanics, but the domain contracts and ranking policies remain distinct. Likewise, `knowledge-service/provenance.py` is useful shared code, but the repository does not currently establish a separately managed Knowledge service merely by having that directory.

### Coverage gaps to close before changing behavior

The existing tests already cover several critical properties, including source separation, exact identifier behavior, index freshness, readiness, and hook request shape. The following still need explicit audit/confirmation as contract-level guarantees before a consolidation:

- Whether `project_root` filtering has direct REST regression cases for both matching and non-matching Knowledge scopes.
- Whether `limit` versus `top_k` precedence and invalid values are tested at the HTTP boundary, including booleans and non-integers.
- Whether deterministic tie-breaking is asserted for the SQLite hybrid scorer, not only implied by implementation.
- Whether candidate identity/deduplication is stable across separate queries and across Memory/Knowledge/Graph.
- Whether the old `/memory/search-hybrid` route has any supported production caller. Current hook tests do not use it.
- Whether the legacy manual RAG hook or standalone semantic utility is installed/referenced by active configuration, as opposed to simply remaining in the repository.

These are audit items, not assertions that the behavior is absent. Search route changes should wait until the tests and install/configuration references settle each item.
