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

const MEMORY_URL = process.env.FLOSSWARE_MEMORY_URL || 'http://127.0.0.1:8767';
const MEMORY_SEARCH_PATH = '/memory/search';
const REQUEST_TIMEOUT_MS = Number.parseInt(process.env.FLOSSWARE_MEMORY_TIMEOUT_MS || '1500', 10);
const MAX_CONTEXT_CHARS = 12000;
const MAX_RESULTS = 10;
const GRAPH_URL = process.env.FLOSSWARE_GRAPH_URL || 'http://127.0.0.1:8766';
const GRAPH_TIMEOUT_MS = Number.parseInt(process.env.FLOSSWARE_GRAPH_TIMEOUT_MS || '400', 10);
const MAX_GRAPH_ROOTS = 3;
const MAX_GRAPH_NODES = 8;
const MAX_GRAPH_CONTEXT_CHARS = 3000;

function buildSearchUrl() {
  return new URL(MEMORY_SEARCH_PATH, MEMORY_URL.endsWith('/') ? MEMORY_URL : `${MEMORY_URL}/`).toString();
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
      lines.push(`[${index + 1}] ${result.content}`);
    }
    lines.push('');
  }

  if (knowledge.length) {
    lines.push('### Relevant Knowledge (what is currently known)');
    for (const [index, result] of knowledge.entries()) {
      lines.push(`[${index + 1}] ${result.content}`);
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

async function graphRequest(route, payload) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), GRAPH_TIMEOUT_MS);
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
  } finally {
    clearTimeout(timer);
  }
}

async function searchGraphContext(cwd) {
  if (typeof cwd !== 'string' || !cwd.trim()) return [];
  const projectName = cwd.replace(/[\\/]+$/, '').split(/[\\/]/).pop() || '';
  if (!projectName || projectName === '.' || projectName === '..') return [];

  const match = await graphRequest('/graph/query', { properties: { name: projectName } });
  const roots = Array.isArray(match?.nodes)
    ? match.nodes.filter(node => node && typeof node.id === 'string').slice(0, MAX_GRAPH_ROOTS)
    : [];
  if (!roots.length) return [];

  const nodes = new Map();
  for (const root of roots) nodes.set(root.id, { node: root, relationship: 'current project' });

  const traversals = await Promise.all(roots.map(root =>
    graphRequest('/graph/traverse', { start: root.id, direction: 'both', max_depth: 1 })
      .catch(error => {
        const message = error instanceof Error ? error.message : String(error);
        process.stderr.write(`Graph context unavailable for project ${projectName}: ${message}\n`);
        return null;
      })
  ));

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

  return [...nodes.values()]
    .slice(0, MAX_GRAPH_NODES)
    .map(({ node, relationship }) => formatGraphNode(node, relationship))
    .filter(Boolean)
    .join('\n')
    .slice(0, MAX_GRAPH_CONTEXT_CHARS)
    .split('\n');
}

async function searchContext(query, source) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

  try {
    const response = await fetch(buildSearchUrl(), {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        accept: 'application/json',
      },
      body: JSON.stringify({ query, limit: MAX_RESULTS, source }),
      signal: controller.signal,
    });

    if (!response.ok) {
      throw new Error(`Memory service returned HTTP ${response.status}`);
    }

    return await response.json();
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

  emitDiagnostics('started', { queryHash, cwdPresent: Boolean(event.cwd) });
  let memoryError = null;
  let graphError = null;
  const [memoryPayload, knowledgePayload, graphContext] = await Promise.all([
    searchContext(query, 'memory').catch(error => {
      const message = error instanceof Error ? error.message : String(error);
      memoryError = message;
      process.stderr.write(`Memory search unavailable: ${message}\n`);
      return null;
    }),
    searchContext(query, 'claude-code').catch(error => {
      const message = error instanceof Error ? error.message : String(error);
      process.stderr.write(`Knowledge search unavailable: ${message}\n`);
      return null;
    }),
    searchGraphContext(event.cwd).catch(error => {
      const message = error instanceof Error ? error.message : String(error);
      graphError = message;
      process.stderr.write(`Graph context unavailable: ${message}\n`);
      return [];
    }),
  ]);

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
    memoryStatus: memoryError ? 'error' : memoryPayload?.ok === true ? 'ok' : 'empty_or_invalid',
    memoryResultCount: results.length,
    memoryCount: memory.length,
    knowledgeCount: knowledge.length,
    graphStatus: graphError ? 'error' : 'ok',
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
