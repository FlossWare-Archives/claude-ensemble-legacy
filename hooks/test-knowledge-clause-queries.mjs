#!/usr/bin/env node
/**
 * Regression: knowledge clause-splitting regex must use real whitespace escapes.
 * Couples to hooks/memory-search-on-prompt.js so production drift fails the test.
 * Double-escaped /\\s+/ on main never matched whitespace, so only the full prompt ran.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const hookPath = path.join(path.dirname(fileURLToPath(import.meta.url)), 'memory-search-on-prompt.js');
const source = fs.readFileSync(hookPath, 'utf8');

// File must contain single-escape whitespace patterns, not the double-escaped main bug.
assert.ok(
  source.includes('query.replace(/\\s+/g'),
  'buildKnowledgeQueries must normalize with /\\s+/ (single escape)',
);
assert.ok(
  !source.includes('query.replace(/\\\\s+/g'),
  'buildKnowledgeQueries must not use double-escaped /\\\\s+/',
);
assert.ok(
  source.includes('[.!?;\\n]'),
  'clause split must include a real \\n escape in the character class',
);
assert.ok(
  !source.includes('[.!?;\\\\n]'),
  'clause split must not double-escape \\\\n',
);

// Timeout labeling contract
assert.ok(source.includes('function isAbortError'), 'isAbortError helper required');
assert.ok(source.includes("error.name === 'AbortError'"), 'AbortError must be detected by name');
assert.ok(
  source.includes("const label = source === 'claude-code' ? 'Knowledge' : 'Memory'"),
  'searchContext must derive Memory/Knowledge label from source',
);
assert.ok(
  source.includes('search timed out after'),
  'search timeout message must include timed out after',
);
assert.ok(source.includes('Graph request timed out after'), 'Graph timeout label required');
assert.ok(source.includes('Knowledge status timed out after'), 'Knowledge status timeout label required');

// Behavioral check: extract the live function from the hook source and run it.
const match = source.match(/function buildKnowledgeQueries\([\s\S]*?\n\}/);
assert.ok(match, 'could not extract buildKnowledgeQueries from hook source');
const buildKnowledgeQueries = vm.runInNewContext(`${match[0]}\nbuildKnowledgeQueries;`);

const prompt = 'What about the Google accomplishments doc, and also the Python packaging notes for CE?';
const queries = buildKnowledgeQueries(prompt, 3);
assert.equal(queries.length, 3, `expected 3 queries, got ${queries.length}: ${JSON.stringify(queries)}`);
assert.ok(queries[0].includes('Google accomplishments'));
assert.ok(queries.some(q => /Python packaging/i.test(q)));
console.log('ok knowledge clause decomposition (source-coupled)');
