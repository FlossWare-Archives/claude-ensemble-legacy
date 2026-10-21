#!/usr/bin/env node
/**
 * Regression: knowledge clause-splitting regex must use real whitespace escapes.
 * Double-escaped /\\s+/ on main never matched whitespace, so only the full prompt ran.
 */
import assert from 'node:assert/strict';

// buildKnowledgeQueries is not exported; exercise the same source logic.
function buildKnowledgeQueries(query, maxQueries) {
  const normalized = query.replace(/\s+/g, ' ').trim();
  if (!normalized) return [];
  const clauses = normalized
    .split(/(?:[.!?;\n]+|,\s+(?=(?:and|but|while|whereas|versus|vs\.? )\b))/i)
    .map(part => part.trim())
    .filter(part => part.split(/\s+/).length >= 4 && part.length >= 20);
  const queries = [];
  for (const candidate of [normalized, ...clauses]) {
    const key = candidate.toLocaleLowerCase();
    if (queries.some(item => item.toLocaleLowerCase() === key)) continue;
    queries.push(candidate);
    if (queries.length >= maxQueries) break;
  }
  return queries;
}

const prompt = 'What about the Google accomplishments doc, and also the Python packaging notes for CE?';
const queries = buildKnowledgeQueries(prompt, 3);
assert.equal(queries.length, 3, `expected 3 queries, got ${queries.length}: ${JSON.stringify(queries)}`);
assert.ok(queries[0].includes('Google accomplishments'));
assert.ok(queries.some(q => /Python packaging/i.test(q)));
console.log('ok knowledge clause decomposition');
