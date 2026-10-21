#!/usr/bin/env node

/**
 * Canonical Claude Code context retrieval hook.
 *
 * UserPromptSubmit is the single prompt-time context retrieval path.
 * Retrieval is relevance-driven: the complete user prompt is the query.
 *
 * The Memory service is shared infrastructure, but results are separated
 * into two contracts. A project Graph lookup adds bounded one-hop relationships
 * based on the working-directory basename when Claude Code provides cwd.
 *   - Memory: episodic/experience records, what happened.
 *   - Knowledge: durable Claude Code documents, what is currently known.
 *
 * The hook is non-blocking and fail-open. It deliberately does not inspect
 * magic prompt keywords such as "remember" or "recall".
 */

// process.getBuiltinModule works both inside this repository's ESM package scope
// and when Claude Code executes the installed hook as a standalone .js file.
const fs = process.getBuiltinModule('fs');
const path = process.getBuiltinModule('path');

const MEMORY_URL = process.env.FLOSSWARE_MEMORY_URL || 'http://127.0.0.1:8767';
const MEMORY_SEARCH_PATH = '/memory/search';
const MAX_CONTEXT_CHARS = 12000;
const MAX_RESULTS = 10;
const GRAPH_URL = process.env.FLOSSWARE_GRAPH_URL || 'http://127.0.0.1:8766';
const DEFAULT_RAG_CONFIG = {
  // Concurrent multi-query searches against the Memory service contend on
  // disk I/O and routinely miss a 1.5s per-request budget. Keep a modest
  // default and prefer sequential primary-then-fallback queries instead.
  memory: { enabled: true, timeout_ms: 3000, max_results: 10 },
  knowledge: { enabled: true, timeout_ms: 3000, max_results: 10, max_queries: 3 },
  graph: { enabled: true, timeout_ms: 800, max_roots: 3, max_nodes: 8, max_context_chars: 3000 },
};
