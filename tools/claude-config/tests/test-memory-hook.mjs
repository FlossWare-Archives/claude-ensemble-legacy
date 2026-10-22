#!/usr/bin/env node
import assert from 'node:assert/strict';
import http from 'node:http';
import { mkdtemp, rm, cp } from 'node:fs/promises';
import fs from 'node:fs';
import os from 'node:os';
import { spawn } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
const hook = path.join(root, 'hooks', 'memory-search-on-prompt.js');

function runHook({ url, graphUrl, input, inputChunks, env = {}, hookPath = hook, cwd = root }) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [hookPath], {
      cwd,
      env: { ...process.env, ...env, FLOSSWARE_MEMORY_URL: url, ...(graphUrl ? { FLOSSWARE_GRAPH_URL: graphUrl } : {}) },
      stdio: ['pipe', 'pipe', 'pipe'],
    });

    let stdout = '';
    let stderr = '';
    child.stdout.on('data', chunk => { stdout += chunk; });
    child.stderr.on('data', chunk => { stderr += chunk; });
    child.on('error', reject);
    child.on('close', code => resolve({ code, stdout, stderr }));

    if (inputChunks) {
      for (const chunk of inputChunks) child.stdin.write(chunk);
      child.stdin.end();
    } else {
      child.stdin.end(JSON.stringify(input));
    }
  });
}

function listen(server) {
  return new Promise((resolve, reject) => {
    server.listen(0, '127.0.0.1', () => resolve(server.address().port));
    server.on('error', reject);
  });
}

function close(server) {
  return new Promise(resolve => server.close(resolve));
}

const requests = [];
const statusRequests = [];
const server = http.createServer((req, res) => {
  let body = '';
  req.on('data', chunk => { body += chunk; });
  req.on('end', () => {
    if (req.url === '/memory/knowledge/status') {
      statusRequests.push({ method: req.method, url: req.url, body });
      res.writeHead(200, { 'content-type': 'application/json' });
      res.end(JSON.stringify({
        ok: true,
        source: 'claude-code',
        status: 'available',
        indexed_document_count: 1,
      }));
      return;
    }
    requests.push({ method: req.method, url: req.url, body });
    res.writeHead(200, { 'content-type': 'application/json' });
    let payload = {
      ok: true,
      results: [
        {
          file: 'project-architecture',
          section: 'Claude Code and Claude Ensemble',
          score: 0.9,
          content: 'Claude Code is the host; Claude Ensemble is the augmentation layer.',
        },
        {
          file: 'project-architecture',
          section: 'Claude Code and Claude Ensemble',
          score: 0.7,
          content: 'Claude Code is the host; Claude Ensemble is the augmentation layer.',
        },
        {
          file: 'claude-code-architecture',
          section: 'durable',
          score: 0.8,
          content: 'Durable project knowledge belongs in the Knowledge contract.',
        },
      ],
    };
    try {
      const query = JSON.parse(body).query;
      if (query === 'empty results') payload.results = [];
      if (query === 'knowledge coverage empty' && JSON.parse(body).source === 'claude-code') payload.results = [];
      if (query === 'knowledge coverage empty' && JSON.parse(body).source === 'memory') payload.results = [];
      if (query === 'bare string result') payload.results = ['Not canonical content'];
      if (query === 'summary only result') payload.results = [{ summary: 'Not canonical Memory content' }];
      if (query === 'invalid content result') payload.results = [{ content: 123 }];
      if (query === 'content and summary result') payload.results = [{ content: 'Canonical content', summary: 'Must be ignored' }];
    } catch {
      // Keep the canonical fixture response for malformed test requests.
    }
    res.end(JSON.stringify(payload));
  });
});

const port = await listen(server);
const graphRequests = [];
const graphServer = http.createServer((req, res) => {
  let body = '';
  req.on('data', chunk => { body += chunk; });
  req.on('end', () => {
    graphRequests.push({ url: req.url, body });
    const request = JSON.parse(body);
    if (req.url === '/graph/query' && request.properties?.name === 'graph-project') {
      res.end(JSON.stringify({ ok: true, nodes: [{ id: 'project-1', type: 'Project', properties: { name: 'graph-project' } }] }));
      return;
    }
    if (req.url === '/graph/traverse') {
      res.writeHead(503, { 'content-type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: 'fixture traversal failure' }));
      return;
    }
    res.writeHead(200, { 'content-type': 'application/json' });
    res.end(JSON.stringify({ ok: true, nodes: [] }));
  });
});
const graphPort = await listen(graphServer);
try {
  const result = await runHook({
    url: `http://127.0.0.1:${port}/`,
    input: {
      hook_event_name: 'UserPromptSubmit',
      prompt: 'What did we decide about Claude Ensemble relative to Claude Code?',
    },
  });

  assert.equal(result.code, 0);
  assert.ok(requests.length >= 2, 'retrieval should query Memory and Knowledge');
  assert.equal(statusRequests.length, 1);
  assert.ok(requests.every(request => request.method === 'POST' && request.url === '/memory/search'));
  const parsedRequests = requests.map(request => JSON.parse(request.body));
  assert.deepEqual([...new Set(parsedRequests.map(request => request.source))].sort(), ['claude-code', 'memory']);
  assert.ok(parsedRequests.every(request => request.limit === 10));
  assert.ok(parsedRequests.some(request =>
    request.source === 'memory' &&
    request.query === 'What did we decide about Claude Ensemble relative to Claude Code?'
  ));
  assert.ok(parsedRequests.some(request =>
    request.source === 'claude-code' &&
    request.query === 'What did we decide about Claude Ensemble relative to Claude Code?'
  ));

  const output = JSON.parse(result.stdout);
  assert.equal(output.hookSpecificOutput.hookEventName, 'UserPromptSubmit');
  assert.match(output.hookSpecificOutput.additionalContext, /Claude Code is the host/);
  assert.match(output.hookSpecificOutput.additionalContext, /Relevant Memory \(what happened\)/);
  assert.match(output.hookSpecificOutput.additionalContext, /Relevant Knowledge \(what is currently known\)/);
  assert.equal(output.hookSpecificOutput.contextRetrieval.memoryCount, 1);
  assert.equal(output.hookSpecificOutput.contextRetrieval.knowledgeCount, 1);

  // The deployed hook is copied outside the repository package scope. It must
  // remain executable there, where package.json cannot make .js files ESM.
  const standaloneDir = await mkdtemp(path.join(os.tmpdir(), 'flossware-memory-hook-'));
  const standaloneHook = path.join(standaloneDir, 'memory-search-on-prompt.js');
  await cp(hook, standaloneHook);
  try {
    const standalone = await runHook({
      url: `http://127.0.0.1:${port}/`,
      hookPath: standaloneHook,
      cwd: standaloneDir,
      input: {
        hook_event_name: 'UserPromptSubmit',
        prompt: 'Tell me about the standalone hook contract',
      },
    });
    assert.equal(standalone.code, 0);
    assert.match(JSON.parse(standalone.stdout).hookSpecificOutput.additionalContext, /augmentation layer/);
  } finally {
    await rm(standaloneDir, { recursive: true, force: true });
  }

  const bareString = await runHook({
    url: `http://127.0.0.1:${port}/`,
    input: { hook_event_name: 'UserPromptSubmit', prompt: 'bare string result' },
  });
  assert.equal(bareString.code, 0);
  assert.equal(bareString.stdout, '');

  const summaryOnly = await runHook({
    url: `http://127.0.0.1:${port}/`,
    input: { hook_event_name: 'UserPromptSubmit', prompt: 'summary only result' },
  });
  assert.equal(summaryOnly.code, 0);
  assert.equal(summaryOnly.stdout, '');

  const invalidContent = await runHook({
    url: `http://127.0.0.1:${port}/`,
    input: { hook_event_name: 'UserPromptSubmit', prompt: 'invalid content result' },
  });
  assert.equal(invalidContent.code, 0);
  assert.equal(invalidContent.stdout, '');

  const contentAndSummary = await runHook({
    url: `http://127.0.0.1:${port}/`,
    input: { hook_event_name: 'UserPromptSubmit', prompt: 'content and summary result' },
  });
  assert.equal(contentAndSummary.code, 0);
  const contentAndSummaryOutput = JSON.parse(contentAndSummary.stdout);
  assert.match(contentAndSummaryOutput.hookSpecificOutput.additionalContext, /Canonical content/);
  assert.doesNotMatch(contentAndSummaryOutput.hookSpecificOutput.additionalContext, /Must be ignored/);

  const utf8Prompt = 'Café architecture';
  const utf8Input = Buffer.from(JSON.stringify({
    hook_event_name: 'UserPromptSubmit',
    prompt: utf8Prompt,
  }), 'utf8');
  const utf8Marker = utf8Input.indexOf(Buffer.from('é', 'utf8'));
  assert.notEqual(utf8Marker, -1);
  const splitUtf8 = await runHook({
    url: `http://127.0.0.1:${port}/`,
    inputChunks: [utf8Input.subarray(0, utf8Marker + 1), utf8Input.subarray(utf8Marker + 1)],
  });
  assert.equal(splitUtf8.code, 0);
  assert.ok(requests.some(request => JSON.parse(request.body).query === 'Café architecture'));

  const longPrompt = 'x'.repeat(13000);
  const longPromptResult = await runHook({
    url: `http://127.0.0.1:${port}/`,
    input: { hook_event_name: 'UserPromptSubmit', prompt: longPrompt },
  });
  assert.equal(longPromptResult.code, 0);
  assert.ok(requests.some(request => JSON.parse(request.body).query === longPrompt));
  assert.match(JSON.parse(longPromptResult.stdout).hookSpecificOutput.additionalContext, /Claude Code is the host/);
  assert.ok(JSON.parse(longPromptResult.stdout).hookSpecificOutput.additionalContext.length <= 12000);

  const ordinaryPrompt = await runHook({
    url: `http://127.0.0.1:${port}/`,
    input: { hook_event_name: 'UserPromptSubmit', prompt: 'Fix the failing test' },
  });
  assert.equal(ordinaryPrompt.code, 0);
  assert.ok(requests.some(request => JSON.parse(request.body).query === 'Fix the failing test'));
  assert.match(JSON.parse(ordinaryPrompt.stdout).hookSpecificOutput.additionalContext, /Relevant Claude Ensemble Context/);

  // Empty retrieval is different from a hook that was never invoked. Opt-in
  // diagnostics report the outcome to stderr without injecting noise into the
  // user's prompt or exposing prompt text / retrieved content.
  const emptyWithDiagnostics = await runHook({
    url: `http://127.0.0.1:${port}/`,
    input: { hook_event_name: 'UserPromptSubmit', prompt: 'empty results' },
    env: { FLOSSWARE_CE_HOOK_DIAGNOSTICS: '1' },
  });
  assert.equal(emptyWithDiagnostics.code, 0);
  assert.equal(emptyWithDiagnostics.stdout, '');
  const emptyDiagnostics = emptyWithDiagnostics.stderr
    .split('\n')
    .filter(line => line.startsWith('CE_HOOK_DIAGNOSTICS '))
    .map(line => JSON.parse(line.slice('CE_HOOK_DIAGNOSTICS '.length)));
  assert.deepEqual(emptyDiagnostics.map(item => item.event), ['started', 'completed']);
  assert.equal(emptyDiagnostics[1].memoryStatus, 'ok');
  assert.equal(emptyDiagnostics[1].memoryResultCount, 0);
  assert.equal(emptyDiagnostics[1].contextEmitted, false);
  assert.equal(JSON.stringify(emptyDiagnostics).includes('empty results'), false);

  const emptyKnowledgeCoverage = await runHook({
    url: `http://127.0.0.1:${port}/`,
    input: { hook_event_name: 'UserPromptSubmit', prompt: 'knowledge coverage empty' },
    env: { FLOSSWARE_CE_HOOK_DIAGNOSTICS: '1' },
  });
  assert.equal(emptyKnowledgeCoverage.code, 0);
  assert.equal(emptyKnowledgeCoverage.stdout, '');
  const emptyKnowledgeDiagnostic = emptyKnowledgeCoverage.stderr
    .split('\n')
    .filter(line => line.startsWith('CE_HOOK_DIAGNOSTICS '))
    .map(line => JSON.parse(line.slice('CE_HOOK_DIAGNOSTICS '.length)))
    .find(item => item.event === 'completed');
  assert.equal(emptyKnowledgeDiagnostic.knowledgeStatus, 'empty');
  assert.equal(emptyKnowledgeDiagnostic.knowledgeResultCount, 0);
  assert.equal(emptyKnowledgeDiagnostic.knowledgeCount, 0);
  assert.equal(JSON.stringify(emptyKnowledgeDiagnostic).includes('knowledge coverage empty'), false);

  const populatedWithDiagnostics = await runHook({
    url: `http://127.0.0.1:${port}/`,
    input: { hook_event_name: 'UserPromptSubmit', prompt: 'ordinary diagnostic prompt' },
    env: { FLOSSWARE_CE_HOOK_DIAGNOSTICS: '1' },
  });
  assert.equal(populatedWithDiagnostics.code, 0);
  assert.ok(JSON.parse(populatedWithDiagnostics.stdout).hookSpecificOutput.additionalContext);
  const populatedDiagnostic = populatedWithDiagnostics.stderr
    .split('\n')
    .filter(line => line.startsWith('CE_HOOK_DIAGNOSTICS '))
    .map(line => JSON.parse(line.slice('CE_HOOK_DIAGNOSTICS '.length)))
    .find(item => item.event === 'completed');
  assert.equal(populatedDiagnostic.contextEmitted, true);
  assert.equal(populatedDiagnostic.memoryCount, 1);
  assert.equal(populatedDiagnostic.knowledgeCount, 1);
  assert.equal(JSON.stringify(populatedDiagnostic).includes('ordinary diagnostic prompt'), false);

  const unavailable = await runHook({
    url: 'http://127.0.0.1:1/',
    input: {
      hook_event_name: 'UserPromptSubmit',
      prompt: 'What architecture decision did we make?',
    },
  });
  assert.equal(unavailable.code, 0);
  assert.equal(unavailable.stdout, '');
  assert.match(unavailable.stderr, /Memory search unavailable/);
  const noProjectMatch = await runHook({
    url: 'http://127.0.0.1:' + port + '/',
    graphUrl: 'http://127.0.0.1:' + graphPort + '/',
    input: { hook_event_name: 'UserPromptSubmit', prompt: 'graph no match', cwd: '/tmp/absent-project' },
    env: { FLOSSWARE_CE_HOOK_DIAGNOSTICS: '1' },
  });
  const noMatchDiagnostic = noProjectMatch.stderr
    .split('\n')
    .filter(line => line.startsWith('CE_HOOK_DIAGNOSTICS '))
    .map(line => JSON.parse(line.slice('CE_HOOK_DIAGNOSTICS '.length)))
    .find(item => item.event === 'completed');
  assert.equal(noMatchDiagnostic.graphStatus, 'no_project_match');
  assert.equal(noMatchDiagnostic.graphCount, 0);

  const traversalFailure = await runHook({
    url: 'http://127.0.0.1:' + port + '/',
    graphUrl: 'http://127.0.0.1:' + graphPort + '/',
    input: { hook_event_name: 'UserPromptSubmit', prompt: 'graph traversal failure', cwd: '/tmp/graph-project' },
    env: { FLOSSWARE_CE_HOOK_DIAGNOSTICS: '1' },
  });
  const traversalDiagnostic = traversalFailure.stderr
    .split('\n')
    .filter(line => line.startsWith('CE_HOOK_DIAGNOSTICS '))
    .map(line => JSON.parse(line.slice('CE_HOOK_DIAGNOSTICS '.length)))
    .find(item => item.event === 'completed');
  assert.equal(traversalDiagnostic.graphStatus, 'error');
  assert.equal(traversalDiagnostic.graphCount, 1);
} finally {
  await close(server);
  await close(graphServer);
}

// Regression: the regexes in buildKnowledgeQueries were double-escaped, so
// whitespace was never normalized and compound prompts never produced clause
// queries; Knowledge only ever searched the full prompt. Exercise the real
// hook and inspect the Knowledge requests it sends.
{
  const clauseRequests = [];
  const clauseServer = http.createServer((req, res) => {
    let body = '';
    req.on('data', chunk => { body += chunk; });
    req.on('end', () => {
      res.writeHead(200, { 'content-type': 'application/json' });
      if (req.url === '/memory/knowledge/status') {
        res.end(JSON.stringify({ ok: true, source: 'claude-code', status: 'available', indexed_document_count: 1 }));
        return;
      }
      clauseRequests.push(JSON.parse(body));
      res.end(JSON.stringify({ ok: true, results: [] }));
    });
  });
  const clausePort = await listen(clauseServer);
  const knowledgeQueriesFor = async prompt => {
    clauseRequests.length = 0;
    const result = await runHook({
      url: `http://127.0.0.1:${clausePort}/`,
      graphUrl: 'http://127.0.0.1:1/',
      input: { hook_event_name: 'UserPromptSubmit', prompt },
    });
    assert.equal(result.code, 0);
    return clauseRequests.filter(request => request.source === 'claude-code').map(request => request.query);
  };
  try {
    const compound = await knowledgeQueriesFor(
      'What did we decide about the memory service boundary,   and how does the installer handle the acceptance profile?'
    );
    assert.ok(compound.includes('What did we decide about the memory service boundary, and how does the installer handle the acceptance profile?'), JSON.stringify(compound));
    assert.ok(compound.includes('What did we decide about the memory service boundary'), JSON.stringify(compound));
    assert.ok(compound.includes('and how does the installer handle the acceptance profile'), JSON.stringify(compound));
    assert.ok(compound.length <= 3, JSON.stringify(compound));

    // The letter "n" must not split a prompt that has no clause boundary.
    const plain = await knowledgeQueriesFor('connection pooling configuration notes');
    assert.deepEqual([...new Set(plain)], ['connection pooling configuration notes']);
  } finally {
    await close(clauseServer);
  }
}

// A service slower than the hook's budget must be reported as a timeout, not as
// the opaque fetch message "This operation was aborted".
{
  const slowSockets = new Set();
  const slowServer = http.createServer(() => {});
  slowServer.on('connection', socket => { slowSockets.add(socket); socket.on('close', () => slowSockets.delete(socket)); });
  const slowPort = await listen(slowServer);
  try {
    const slow = await runHook({
      url: 'http://127.0.0.1:' + slowPort + '/',
      graphUrl: 'http://127.0.0.1:1/',
      input: { hook_event_name: 'UserPromptSubmit', prompt: 'slow memory service' },
      env: { FLOSSWARE_MEMORY_TIMEOUT_MS: '150' },
    });
    assert.equal(slow.code, 0);
    assert.equal(slow.stdout, '');
    assert.match(slow.stderr, /Memory search request timed out after 150ms/);
    assert.match(slow.stderr, /Knowledge search request timed out after 150ms/);
    assert.doesNotMatch(slow.stderr, /operation was aborted/);
  } finally {
    for (const socket of slowSockets) socket.destroy();
    await close(slowServer);
  }
}


 // Regression: factual Knowledge retrieval must issue a compact keyword query
 // and rank its strongest result above weaker results from the full prompt.
{
  const retrievalRequests = [];
  const retrievalServer = http.createServer((req, res) => {
    let body = '';
    req.on('data', chunk => { body += chunk; });
    req.on('end', () => {
      const request = JSON.parse(body);
      if (req.url === '/memory/knowledge/status') {
        res.writeHead(200, { 'content-type': 'application/json' });
        res.end(JSON.stringify({ ok: true, source: 'claude-code', status: 'available', indexed_document_count: 11 }));
        return;
      }
      retrievalRequests.push(request);
      res.writeHead(200, { 'content-type': 'application/json' });
      if (request.source === 'memory') {
        res.end(JSON.stringify({ ok: true, results: [] }));
        return;
      }
      if (request.query === 'install root default Claude Ensemble') {
        res.end(JSON.stringify({
          ok: true,
          results: [{
            file: 'claude-code-deployment-default',
            section: 'Install root',
            score: 0.99,
            content: 'Claude Ensemble default install root: ~/.FlossWare/claude-ensemble.',
          }],
        }));
        return;
      }
      const results = Array.from({ length: 10 }, (_, index) => ({
        file: 'claude-code-unrelated-' + index,
        section: 'Unrelated',
        score: 0.95 - index * 0.01,
        content: 'Unrelated deployment note number ' + index + '.',
      }));
      res.end(JSON.stringify({ ok: true, results }));
    });
  });
  const retrievalPort = await listen(retrievalServer);
  try {
    const prompt = 'What install root did we establish as the default for Claude Ensemble?';
    const result = await runHook({
      url: `http://127.0.0.1:${retrievalPort}/`,
      graphUrl: 'http://127.0.0.1:1/',
      input: { hook_event_name: 'UserPromptSubmit', prompt },
    });
    assert.equal(result.code, 0);
    const knowledgeQueries = retrievalRequests
      .filter(request => request.source === 'claude-code')
      .map(request => request.query);
    assert.ok(knowledgeQueries.includes('install root default Claude Ensemble'), JSON.stringify(knowledgeQueries));
    const context = JSON.parse(result.stdout).hookSpecificOutput.additionalContext;
    assert.ok(context.includes('~/.FlossWare/claude-ensemble'));
    assert.ok(context.indexOf('~/.FlossWare/claude-ensemble') < context.indexOf('Unrelated deployment note'));
  } finally {
    await close(retrievalServer);
  }
}

console.log('memory hook REST tests passed');
