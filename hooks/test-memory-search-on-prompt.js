#!/usr/bin/env node

const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const http = require('node:http');
const path = require('node:path');
const { test } = require('node:test');

test('compound prompt retrieves Knowledge through a decomposed clause', async () => {
  const queries = [];
  const server = http.createServer((request, response) => {
    if (request.method === 'POST' && request.url === '/memory/knowledge/status') {
      response.writeHead(200, { 'content-type': 'application/json' });
      response.end(JSON.stringify({
        ok: true,
        source: 'claude-code',
        status: 'available',
        indexed_document_count: 1,
      }));
      return;
    }
    if (request.method !== 'POST' || request.url !== '/memory/search') {
      response.writeHead(404).end();
      return;
    }
    let body = '';
    request.setEncoding('utf8');
    request.on('data', chunk => { body += chunk; });
    request.on('end', () => {
      const payload = JSON.parse(body);
      queries.push(payload);
      const isTargetClause = payload.source === 'claude-code'
        && payload.query.toLowerCase().startsWith('and does sessionend');
      response.writeHead(200, { 'content-type': 'application/json' });
      response.end(JSON.stringify({
        ok: true,
        results: isTargetClause ? [{
          file: 'claude-code-lifecycle',
          source: 'claude-code',
          section: 'SessionEnd',
          content: 'SessionEnd captures metadata only and does not establish task quality.',
          score: 1,
        }] : [],
      }));
    });
  });

  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const address = server.address();
  const hookPath = path.join(__dirname, 'memory-search-on-prompt.js');
  const child = spawn(process.execPath, [hookPath], {
    env: {
      ...process.env,
      FLOSSWARE_MEMORY_URL: `http://127.0.0.1:${address.port}`,
      FLOSSWARE_GRAPH_URL: 'http://127.0.0.1:1',
      FLOSSWARE_CE_HOOK_DIAGNOSTICS: '1',
      FLOSSWARE_MEMORY_TIMEOUT_MS: '1000',
      FLOSSWARE_GRAPH_TIMEOUT_MS: '50',
    },
    stdio: ['pipe', 'pipe', 'pipe'],
  });
  let stdout = '';
  let stderr = '';
  child.stdout.setEncoding('utf8').on('data', chunk => { stdout += chunk; });
  child.stderr.setEncoding('utf8').on('data', chunk => { stderr += chunk; });
  child.stdin.end(JSON.stringify({
    prompt: 'Explain Memory versus Knowledge, and does SessionEnd establish task quality?',
  }));

  try {
    const exitCode = await new Promise((resolve, reject) => {
      child.once('error', reject);
      child.once('close', resolve);
    });
    assert.equal(exitCode, 0, stderr);
    const submittedQueries = queries
      .filter(item => item.source === 'claude-code')
      .map(item => item.query);
    assert.ok(submittedQueries.some(query => query.toLowerCase().startsWith('and does sessionend')));
    const output = JSON.parse(stdout);
    assert.match(output.hookSpecificOutput.additionalContext, /SessionEnd captures metadata only/);
    assert.equal(output.hookSpecificOutput.contextRetrieval.knowledgeCount, 1);
    assert.doesNotMatch(stderr, /Knowledge index status unavailable:/);
    const diagnosticLine = stderr.split('\\n').find(line => line.startsWith('CE_HOOK_DIAGNOSTICS '));
    assert.ok(diagnosticLine, 'opt-in diagnostics should be emitted');
    const diagnostics = JSON.parse(diagnosticLine.slice('CE_HOOK_DIAGNOSTICS '.length));
    assert.equal(diagnostics.knowledgeIndexStatus, 'available');
    assert.equal(diagnostics.knowledgeIndexedDocumentCount, 1);
  } finally {
    server.close();
  }
});
