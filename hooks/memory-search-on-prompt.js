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
  memory: { enabled: true, timeout_ms: 1500, max_results: 10 },
  knowledge: { enabled: true, timeout_ms: 1500, max_results: 10, max_queries: 3 },
  graph: { enabled: true, timeout_ms: 400, max_roots: 3, max_nodes: 8, max_context_chars: 3000 },
};

function ragConfigPath() {
  return process.env.FLOSSWARE_CE_RAG_CONFIG || path.resolve(path.dirname(process.argv[1]), '..', '.flossware-claude-config', 'rag.json');
}

function loadRagConfig() {
  let supplied = {};
  try { if (fs.existsSync(ragConfigPath())) supplied = JSON.parse(fs.readFileSync(ragConfigPath(), 'utf8')); }
  catch (error) { return { error: 'cannot read ' + ragConfigPath() + ': ' + error.message }; }
  if (!supplied || typeof supplied !== 'object' || Array.isArray(supplied)) return { error: 'configuration must be a JSON object' };
  const config = JSON.parse(JSON.stringify(DEFAULT_RAG_CONFIG));
  for (const section of Object.keys(supplied)) {
    if (!Object.hasOwn(config, section) || !supplied[section] || typeof supplied[section] !== 'object' || Array.isArray(supplied[section])) return { error: 'unknown or invalid section: ' + section };
    for (const key of Object.keys(supplied[section])) {
      if (!Object.hasOwn(config[section], key)) return { error: 'unknown setting: ' + section + '.' + key };
      config[section][key] = supplied[section][key];
    }
  }
  const ranges = {
    'memory.timeout_ms': [50, 10000], 'memory.max_results': [1, 10],
    'knowledge.timeout_ms': [50, 10000], 'knowledge.max_results': [1, 10], 'knowledge.max_queries': [1, 3],
    'graph.timeout_ms': [50, 5000], 'graph.max_roots': [1, 3], 'graph.max_nodes': [1, 8], 'graph.max_context_chars': [100, 3000],
  };
  for (const [section, values] of Object.entries(config)) {
    if (typeof values.enabled !== 'boolean') return { error: section + '.enabled must be boolean' };
    for (const [key, value] of Object.entries(values)) {
      if (key === 'enabled') continue;
      const range = ranges[section + '.' + key];
      if (!range || !Number.isInteger(value) || value < range[0] || value > range[1]) return { error: section + '.' + key + ' is outside its supported range' };
    }
  }
  if (process.env.FLOSSWARE_MEMORY_TIMEOUT_MS !== undefined) {
    const value = Number.parseInt(process.env.FLOSSWARE_MEMORY_TIMEOUT_MS, 10);
    if (!Number.isInteger(value) || value < 50 || value > 10000) return { error: 'FLOSSWARE_MEMORY_TIMEOUT_MS must be 50..10000' };
    config.memory.timeout_ms = value; config.knowledge.timeout_ms = value;
  }
  if (process.env.FLOSSWARE_GRAPH_TIMEOUT_MS !== undefined) {
    const value = Number.parseInt(process.env.FLOSSWARE_GRAPH_TIMEOUT_MS, 10);
    if (!Number.isInteger(value) || value < 50 || value > 5000) return { error: 'FLOSSWARE_GRAPH_TIMEOUT_MS must be 50..5000' };
    config.graph.timeout_ms = value;
  }
  return { config };
}

function buildSearchUrl() {
  return new URL(MEMORY_SEARCH_PATH, MEMORY_URL.endsWith('/') ? MEMORY_URL : `${MEMORY_URL}/`).toString();
}

function buildKnowledgeStatusUrl() {
  return new URL('/memory/knowledge/status', MEMORY_URL.endsWith('/') ? MEMORY_URL : `${MEMORY_URL}/`).toString();
}

function resultText(result) {
  if (!result || typeof result !== 'object') return '';
  if (typeof result.content !== 'string') return '';
  return result.content.trim();
}

function resultSource(result) {
  const file = typeof result?.file === 'string' ? result.file : '';
  const source = typeof result?.source === 'string' ? result.source : '';
  return source === 'claude-code' || file.startsWith('claude-code-') ? 'knowledge' : 'memory';
}

function dedupeResults(results) {
  const seen = new Set();
  const deduped = [];

  for (const result of results) {
    const content = resultText(result);
    if (!content) continue;

    const key = [
      resultSource(result),
      result.file || '',
      result.section || '',
      content,
    ].join('\u0000');

    if (seen.has(key)) continue;
    seen.add(key);
    deduped.push({ ...result, content });
  }

  return deduped;
}

function extractResults(payload) {
  if (!payload || typeof payload !== 'object' || payload.ok !== true) return [];
  return Array.isArray(payload.results) ? dedupeResults(payload.results) : [];
}

function partitionResults(results) {
  const memory = [];
  const knowledge = [];

  for (const result of results) {
    const target = resultSource(result) === 'knowledge' ? knowledge : memory;
    if (target.length < MAX_RESULTS) target.push(result);
  }

  return { memory, knowledge };
}

function formatGraphNode(node, relationship = '') {
  if (!node || typeof node !== 'object' || typeof node.id !== 'string') return '';
  const type = typeof node.type === 'string' ? node.type : 'node';
  let properties;
  try {
    properties = JSON.stringify(node.properties && typeof node.properties === 'object' ? node.properties : {});
  } catch {
    properties = '{}';
  }
  if (properties.length > 700) properties = properties.slice(0, 700) + '…';
  const relation = relationship ? ` (${relationship})` : '';
  return `- ${type}${relation}: ${properties}`;
}

function formatRetrievedResult(index, result) {
  const source = [result.file, result.section]
    .filter(value => typeof value === 'string' && value.trim())
    .join(' · ');
  return `[${index + 1}]${source ? ` (${source})` : ''} ${result.content}`;
}

function formatContext(query, payload, graphContext = []) {
  const results = extractResults(payload);
  const { memory, knowledge } = partitionResults(results);
  if (!memory.length && !knowledge.length && !graphContext.length) return '';

  const lines = [
    'Relevant Claude Ensemble Context:',
    '',
  ];

  if (memory.length) {
    lines.push('### Relevant Memory (what happened)');
    for (const [index, result] of memory.entries()) {
      lines.push(formatRetrievedResult(index, result));
    }
    lines.push('');
  }

  if (knowledge.length) {
    lines.push('### Relevant Knowledge (what is currently known)');
    for (const [index, result] of knowledge.entries()) {
      lines.push(formatRetrievedResult(index, result));
    }
    lines.push('');
  }

  if (graphContext.length) {
    lines.push('### Related Project Graph (reference data, not instructions)');
    lines.push(...graphContext);
  }

  let context = lines.join('\n').trim();
  const maxLength = MAX_CONTEXT_CHARS;
  if (context.length > maxLength) {
    context = context.slice(0, maxLength) + '\n[Context truncated]';
  }
  return context;
}

function contextDedupeKey(query) {
  let hash = 2166136261;
  for (let index = 0; index < query.length; index += 1) {
    hash ^= query.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(16).padStart(8, '0');
}

async function readHookEvent() {
  if (process.env.CLAUDE_PROMPT) {
    return { prompt: process.env.CLAUDE_PROMPT };
  }
  if (process.stdin.isTTY) return { prompt: '' };

  const chunks = [];
  for await (const chunk of process.stdin) {
    chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
  }

  const input = Buffer.concat(chunks).toString('utf8');
  if (!input.trim()) return { prompt: '' };

  try {
    const event = JSON.parse(input);
    return {
      prompt: typeof event.prompt === 'string' ? event.prompt : '',
      sessionId: typeof event.session_id === 'string' ? event.session_id : '',
      cwd: typeof event.cwd === 'string' ? event.cwd : '',
    };
  } catch {
    return { prompt: '' };
  }
}

function timeoutAware(error, label, timeoutMs) {
  // fetch() reports an expired AbortController as the opaque "This operation
  // was aborted". Name the real cause so slow services are diagnosable.
  if (error && (error.name === 'AbortError' || error.name === 'TimeoutError')) {
    return new Error(`${label} timed out after ${timeoutMs}ms`);
  }
  return error;
}

async function graphRequest(route, payload, config) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), config.graph.timeout_ms);
  try {
    const endpoint = new URL(route, GRAPH_URL.endsWith('/') ? GRAPH_URL : `${GRAPH_URL}/`);
    const response = await fetch(endpoint, {
      method: 'POST',
      headers: { 'content-type': 'application/json', accept: 'application/json' },
      body: JSON.stringify(payload),
      signal: controller.signal,
    });
    if (!response.ok) throw new Error(`Graph service returned HTTP ${response.status}`);
    const result = await response.json();
    return result && result.ok === true ? result : null;
  } catch (error) {
    throw timeoutAware(error, 'Graph request ' + route, config.graph.timeout_ms);
  } finally {
    clearTimeout(timer);
  }
}

async function searchGraphContext(cwd, config) {
  if (!config.graph.enabled) return { lines: [], status: 'disabled' };
  if (typeof cwd !== 'string' || !cwd.trim()) return { lines: [], status: 'skipped_no_cwd' };
  const projectName = cwd.replace(/[\\/]+$/, '').split(/[\\/]/).pop() || '';
  if (!projectName || projectName === '.' || projectName === '..') return { lines: [], status: 'skipped_invalid_cwd' };

  const match = await graphRequest('/graph/query', { properties: { name: projectName } }, config);
  const roots = Array.isArray(match?.nodes)
    ? match.nodes.filter(node => node && typeof node.id === 'string').slice(0, config.graph.max_roots)
    : [];
  if (!roots.length) return { lines: [], status: 'no_project_match' };

  const nodes = new Map();
  for (const root of roots) nodes.set(root.id, { node: root, relationship: 'current project' });

  const traversals = await Promise.all(roots.map(root =>
    graphRequest('/graph/traverse', { start: root.id, direction: 'both', max_depth: 1 }, config)
      .catch(error => {
        const message = error instanceof Error ? error.message : String(error);
        process.stderr.write(`Graph context unavailable for project ${projectName}: ${message}\n`);
        return null;
      })
  ));

  const failedTraversals = traversals.filter(traversal => traversal === null).length;
  for (const traversal of traversals) {
    for (const item of Array.isArray(traversal?.results) ? traversal.results : []) {
      const node = item?.node;
      if (!node || typeof node.id !== 'string' || nodes.has(node.id)) continue;
      const relationship = typeof item?.via?.type === 'string'
        ? item.via.type
        : typeof item?.via?.relationship === 'string' ? item.via.relationship : 'related';
      nodes.set(node.id, { node, relationship });
    }
  }

  const lines = [...nodes.values()]
    .slice(0, config.graph.max_nodes)
    .map(({ node, relationship }) => formatGraphNode(node, relationship))
    .filter(Boolean)
    .join('\n')
    .slice(0, config.graph.max_context_chars)
    .split('\n')
    .filter(Boolean);
  return {
    lines,
    status: failedTraversals === traversals.length ? 'error' : failedTraversals ? 'partial_error' : 'ok',
  };
}

async function searchContext(query, source, config) {
  const controller = new AbortController();
  const section = source === 'claude-code' ? config.knowledge : config.memory;
  const timer = setTimeout(() => controller.abort(), section.timeout_ms);

  try {
    const response = await fetch(buildSearchUrl(), {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        accept: 'application/json',
      },
      body: JSON.stringify({ query, limit: section.max_results, source }),
      signal: controller.signal,
    });

    if (!response.ok) {
      throw new Error(`Memory service returned HTTP ${response.status}`);
    }

    return await response.json();
  } catch (error) {
    throw timeoutAware(error, (source === 'claude-code' ? 'Knowledge' : 'Memory') + ' search request', section.timeout_ms);
  } finally {
    clearTimeout(timer);
  }
}

function buildKnowledgeQueries(query, maxQueries) {
  const normalized = query.replace(/\s+/g, ' ').trim();
  if (!normalized) return [];

  // The Memory service uses lexical similarity rather than embeddings. Search
  // the complete prompt, meaningful clauses, and a compact keyword query. The
  // latter removes question framing so short factual questions still match
  // documents whose wording differs from the prompt.
  const clauses = normalized
    .split(/(?:[.!?;\n]+|,\s+(?=(?:and|but|while|whereas|versus|vs\.? )\b))/i)
    .map(part => part.trim())
    .filter(part => part.split(/\s+/).length >= 4 && part.length >= 20);
  const stopWords = new Set([
    'a', 'an', 'and', 'are', 'as', 'at', 'be', 'by', 'can', 'did', 'do',
    'does', 'for', 'from', 'have', 'how', 'i', 'in', 'is', 'it', 'me',
    'of', 'on', 'or', 'our', 'please', 'that', 'the', 'this', 'to', 'was',
    'we', 'were', 'what', 'when', 'where', 'which', 'who', 'why', 'with',
    'you', 'your', 'establish', 'established',
  ]);
  const keywords = normalized
    .replace(/[?!.;,]+/g, ' ')
    .split(/\s+/)
    .filter(word => word && !stopWords.has(word.toLocaleLowerCase()))
    .join(' ');
  const queries = [];
  for (const candidate of [normalized, ...clauses, keywords]) {
    if (!candidate) continue;
    const key = candidate.toLocaleLowerCase();
    if (queries.some(item => item.toLocaleLowerCase() === key)) continue;
    queries.push(candidate);
    if (queries.length >= maxQueries) break;
  }
  return queries;
}

function buildMemoryQueries(query, maxQueries = 3) {
  const normalized = query.replace(/\s+/g, ' ').trim();
  if (!normalized) return [];

  // Historical references are often described conversationally rather than
  // with the exact title used when they were saved. Search the original prompt,
  // a version without conversational framing, and a compact keyword query.
  const stripped = normalized
    .replace(/^(?:(?:hey\s+)?(?:can|could|would)\s+you\s+|please\s+|what about\s+|what do you remember about\s+|what have you remembered about\s+|tell me about\s+|look up\s+|search for\s+|find\s+)/i, '')
    .replace(/[?!.]+$/, '')
    .trim();
  const stopWords = new Set([
    'a', 'an', 'and', 'are', 'about', 'do', 'does', 'for', 'have', 'i',
    'is', 'it', 'me', 'of', 'please', 'the', 'this', 'to', 'what', 'you',
    'your', 'remember', 'remembered', 'thing', 'things', 'document', 'doc',
  ]);
  const keywords = stripped.split(/\s+/).filter(word => !stopWords.has(word.toLocaleLowerCase())).join(' ');
  const candidates = [normalized, stripped, keywords];
  const queries = [];
  for (const candidate of candidates) {
    if (!candidate) continue;
    if (queries.some(item => item.toLocaleLowerCase() === candidate.toLocaleLowerCase())) continue;
    queries.push(candidate);
    if (queries.length >= maxQueries) break;
  }
  return queries;
}

async function searchMemoryContext(query, config) {
  const queries = buildMemoryQueries(query);
  const responses = await Promise.all(queries.map(candidate =>
    searchContext(candidate, 'memory', config).catch(error => ({ error }))
  ));
  const successful = responses.filter(response => !response.error && response?.ok === true);
  if (!successful.length && responses.some(response => response.error)) {
    throw responses.find(response => response.error).error;
  }

  // Interleave results from each query so the first broad query cannot crowd
  // every useful result out of the keyword-specific searches.
  const merged = [];
  const maxLength = Math.max(0, ...successful.map(response =>
    Array.isArray(response.results) ? response.results.length : 0
  ));
  for (let index = 0; index < maxLength; index += 1) {
    for (const response of successful) {
      if (Array.isArray(response.results) && response.results[index]) {
        merged.push(response.results[index]);
      }
    }
  }
  return { ok: successful.length > 0, results: dedupeResults(merged).slice(0, config.memory.max_results) };
}

async function searchKnowledgeContext(query, config) {
  if (!config.knowledge.enabled) return { ok: true, results: [], disabled: true };
  const queries = buildKnowledgeQueries(query, config.knowledge.max_queries);
  if (!queries.length) return { ok: true, results: [] };

  const responses = await Promise.all(queries.map(candidate =>
    searchContext(candidate, 'claude-code', config).catch(error => ({ error }))
  ));
  const successful = responses.filter(response => !response.error && response?.ok === true);
  if (!successful.length && responses.some(response => response.error)) {
    throw responses.find(response => response.error).error;
  }

  // Scores from the same Memory index are comparable across queries. Rank
  // the union instead of letting results from the full prompt crowd out a
  // stronger match found by the compact keyword query.
  const byKey = new Map();
  let ordinal = 0;
  for (const response of successful) {
    for (const raw of Array.isArray(response.results) ? response.results : []) {
      const content = resultText(raw);
      if (!content) continue;
      const result = { ...raw, content, _ordinal: ordinal++ };
      const key = [
        resultSource(result),
        result.file || '',
        result.section || '',
        content,
      ].join('\u0000');
      const previous = byKey.get(key);
      const score = Number.isFinite(result.score) ? result.score : 0;
      const previousScore = previous && Number.isFinite(previous.score) ? previous.score : 0;
      if (!previous || score > previousScore) byKey.set(key, result);
    }
  }
  const ranked = [...byKey.values()]
    .sort((left, right) => {
      const leftScore = Number.isFinite(left.score) ? left.score : 0;
      const rightScore = Number.isFinite(right.score) ? right.score : 0;
      return rightScore - leftScore || left._ordinal - right._ordinal;
    })
    .slice(0, MAX_RESULTS)
    .map(({ _ordinal, ...result }) => result);
  return { ok: successful.length > 0, results: ranked };
}

async function searchKnowledgeStatus(config) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), config.knowledge.timeout_ms);
  try {
    const response = await fetch(buildKnowledgeStatusUrl(), {
      method: 'POST',
      headers: { 'content-type': 'application/json', accept: 'application/json' },
      body: '{}',
      signal: controller.signal,
    });
    if (!response.ok) throw new Error(`Knowledge status returned HTTP ${response.status}`);
    const result = await response.json();
    if (!result || result.ok !== true
      || !['available', 'empty'].includes(result.status)
      || !Number.isInteger(result.indexed_document_count)
      || result.indexed_document_count < 0) {
      throw new Error('Knowledge status response did not satisfy the source contract');
    }
    return result;
  } catch (error) {
    throw timeoutAware(error, 'Knowledge status request', config.knowledge.timeout_ms);
  } finally {
    clearTimeout(timer);
  }
}

function emitDiagnostics(eventName, details) {
  if (process.env.FLOSSWARE_CE_HOOK_DIAGNOSTICS !== '1') return;
  // Never log prompt text or retrieved content. The hash correlates one prompt
  // across diagnostics without making the prompt itself observable.
  process.stderr.write(`CE_HOOK_DIAGNOSTICS ${JSON.stringify({ event: eventName, ...details })}\n`);
}

async function main() {
  const event = await readHookEvent();
  const query = typeof event.prompt === 'string' ? event.prompt.trim() : '';
  const queryHash = contextDedupeKey(query);
  if (!query) {
    emitDiagnostics('skipped', { reason: 'empty_prompt', queryHash });
    return;
  }

  const loadedConfig = loadRagConfig();
  if (loadedConfig.error) {
    process.stderr.write('RAG configuration invalid: ' + loadedConfig.error + '\n');
    emitDiagnostics('configuration_error', { queryHash, error: loadedConfig.error });
    return;
  }
  const config = loadedConfig.config;
  emitDiagnostics('started', { queryHash, cwdPresent: Boolean(event.cwd) });
  let memoryError = null;
  let graphError = null;
  let knowledgeIndexError = null;
  const [memoryPayload, knowledgePayload, graphResult, knowledgeIndex] = await Promise.all([
    config.memory.enabled ? searchMemoryContext(query, config).catch(error => {
      const message = error instanceof Error ? error.message : String(error);
      memoryError = message;
      process.stderr.write('Memory search unavailable: ' + message + '\n');
      return null;
    }) : Promise.resolve({ ok: true, results: [], disabled: true }),
    searchKnowledgeContext(query, config).catch(error => {
      const message = error instanceof Error ? error.message : String(error);
      process.stderr.write('Knowledge search unavailable: ' + message + '\n');
      return null;
    }),
    searchGraphContext(event.cwd, config).catch(error => {
      const message = error instanceof Error ? error.message : String(error);
      graphError = message;
      process.stderr.write('Graph context unavailable: ' + message + '\n');
      return { lines: [], status: 'error' };
    }),
    config.knowledge.enabled ? searchKnowledgeStatus(config).catch(error => {
      const message = error instanceof Error ? error.message : String(error);
      knowledgeIndexError = message;
      process.stderr.write('Knowledge index status unavailable: ' + message + '\n');
      return null;
    }) : Promise.resolve({ status: 'disabled', indexed_document_count: null }),
  ]);

  const graphContext = graphResult.lines;
  const memoryResults = extractResults(memoryPayload)
    .filter(result => resultSource(result) === 'memory');
  const knowledgeResults = extractResults(knowledgePayload)
    .filter(result => resultSource(result) === 'knowledge')
    .map(result => ({ ...result, source: 'claude-code' }));
  const payload = {
    ok: memoryPayload?.ok === true || knowledgePayload?.ok === true,
    results: [...memoryResults, ...knowledgeResults],
  };
  const results = extractResults(payload);
  const { memory, knowledge } = partitionResults(results);
  const additionalContext = formatContext(query, payload, graphContext);
  const diagnostics = {
    queryHash,
    memoryStatus: !config.memory.enabled ? 'disabled' : memoryError ? 'error' : memoryPayload?.ok === true ? 'ok' : 'empty_or_invalid',
    memoryResultCount: memoryResults.length,
    memoryCount: memory.length,
    knowledgeStatus: !config.knowledge.enabled ? 'disabled' : knowledgePayload?.ok !== true ? 'error' : knowledgeResults.length ? 'ok' : 'empty',
    knowledgeResultCount: knowledgeResults.length,
    knowledgeCount: knowledge.length,
    knowledgeSource: 'claude-code',
    knowledgeIndexStatus: !config.knowledge.enabled ? 'disabled' : knowledgeIndexError ? 'error' : knowledgeIndex?.status || 'unavailable',
    knowledgeIndexedDocumentCount: Number.isInteger(knowledgeIndex?.indexed_document_count)
      ? knowledgeIndex.indexed_document_count : null,
    graphStatus: !config.graph.enabled ? 'disabled' : graphError ? 'error' : graphResult.status,
    graphCount: graphContext.length,
    contextEmitted: Boolean(additionalContext),
  };
  if (!additionalContext) {
    emitDiagnostics('completed', diagnostics);
    return;
  }

  emitDiagnostics('completed', diagnostics);
  process.stdout.write(JSON.stringify({
    hookSpecificOutput: {
      hookEventName: 'UserPromptSubmit',
      additionalContext,
      contextRetrieval: {
        queryHash,
        memoryCount: memory.length,
        knowledgeCount: knowledge.length,
        graphCount: graphContext.length,
      },
    },
  }) + '\n');
}
main().catch(error => {
  const message = error instanceof Error ? error.message : String(error);
  process.stderr.write(`Memory search unavailable: ${message}\n`);
});
