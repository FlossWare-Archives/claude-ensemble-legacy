#!/usr/bin/env node
import assert from 'node:assert/strict';
import http from 'node:http';
import { mkdtemp, rm, cp } from 'node:fs/promises';
import os from 'node:os';
import { spawn } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
const hook = path.join(root, 'hooks', 'memory-search-on-prompt.js');

function runHook({ url, input, inputChunks, env = {}, hookPath = hook, cwd = root }) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [hookPath], {
      cwd,
      env: { ...process.env, ...env, FLOSSWARE_MEMORY_URL: url },
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
const server = http.createServer((req, res) => {
  let body = '';
  req.on('data', chunk => { body += chunk; });
  req.on('end', () => {
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
try {
  const result = await runHook({
    url: `http://127.0.0.1:${port}/`,
    input: {
      hook_event_name: 'UserPromptSubmit',
      prompt: 'What did we decide about Claude Ensemble relative to Claude Code?',
    },
  });

  assert.equal(result.code, 0);
  assert.equal(requests.length, 2);
  assert.ok(requests.every(request => request.method === 'POST' && request.url === '/memory/search'));
  assert.deepEqual(requests.map(request => JSON.parse(request.body).source).sort(), ['claude-code', 'memory']);
  assert.ok(requests.every(request =>
    JSON.parse(request.body).query === 'What did we decide about Claude Ensemble relative to Claude Code?' &&
    JSON.parse(request.body).limit === 10
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
  assert.equal(JSON.parse(requests.at(-1).body).query, 'Café architecture');

  const longPrompt = 'x'.repeat(13000);
  const longPromptResult = await runHook({
    url: `http://127.0.0.1:${port}/`,
    input: { hook_event_name: 'UserPromptSubmit', prompt: longPrompt },
  });
  assert.equal(longPromptResult.code, 0);
  assert.equal(JSON.parse(requests.at(-1).body).query, longPrompt);
  assert.match(JSON.parse(longPromptResult.stdout).hookSpecificOutput.additionalContext, /Claude Code is the host/);
  assert.ok(JSON.parse(longPromptResult.stdout).hookSpecificOutput.additionalContext.length <= 12000);

  const ordinaryPrompt = await runHook({
    url: `http://127.0.0.1:${port}/`,
    input: { hook_event_name: 'UserPromptSubmit', prompt: 'Fix the failing test' },
  });
  assert.equal(ordinaryPrompt.code, 0);
  assert.equal(JSON.parse(requests.at(-1).body).query, 'Fix the failing test');
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
} finally {
  await close(server);
}

console.log('memory hook REST tests passed');
