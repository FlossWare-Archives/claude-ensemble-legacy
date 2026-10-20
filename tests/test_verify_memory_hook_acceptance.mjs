#!/usr/bin/env node

import assert from 'node:assert/strict';
import http from 'node:http';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import os from 'node:os';
import { spawn } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const scriptPath = path.join(repoRoot, 'scripts', 'verify-memory-hook-acceptance.cjs');

function runAcceptanceScript({ url, args = [], env = {}, cwd = repoRoot }) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [scriptPath, ...args], {
      cwd,
      env: {
        ...process.env,
        FLOSSWARE_MEMORY_URL: url,
        // Unit tests explicitly exercise the repository fixture. The operational
        // script itself defaults to the managed installed profile.
        FLOSSWARE_HOOK_PATH: path.join(repoRoot, 'hooks', 'memory-search-on-prompt.js'),
        ...env,
      },
      stdio: ['pipe', 'pipe', 'pipe'],
    });

    let stdout = '';
    let stderr = '';
    child.stdout.on('data', chunk => { stdout += chunk; });
    child.stderr.on('data', chunk => { stderr += chunk; });
    child.on('error', reject);
    child.on('close', code => resolve({ code, stdout, stderr }));
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

let mockMode = 'ok';

const server = http.createServer((req, res) => {
  let body = '';
  req.on('data', chunk => { body += chunk; });
  req.on('end', () => {
    if (mockMode === 'service_unavailable') {
      res.writeHead(503, { 'content-type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: 'Service Unavailable' }));
      return;
    }

    if (req.url === '/health') {
      res.writeHead(200, { 'content-type': 'application/json' });
      res.end(JSON.stringify({ ok: true }));
      return;
    }

    if (req.url === '/memory/knowledge/status') {
      res.writeHead(200, { 'content-type': 'application/json' });
      res.end(JSON.stringify({
        ok: true,
        source: 'claude-code',
        status: 'available',
        indexed_document_count: 1,
      }));
      return;
    }

    if (req.url === '/memory/search') {
      res.writeHead(200, { 'content-type': 'application/json' });
      const results = [];

      if (mockMode === 'both_present' || mockMode === 'memory_only' || mockMode === 'headings_only') {
        results.push({
          file: 'decision.md',
          section: 'Memory',
          source: 'memory',
          content: mockMode === 'headings_only'
            ? 'Generic memory item without expected string'
            : 'Seeded Acceptance Test Memory Record Content',
        });
      }

      if (mockMode === 'both_present' || mockMode === 'knowledge_only' || mockMode === 'headings_only') {
        results.push({
          file: 'claude-code-arch.md',
          section: 'Knowledge',
          source: 'claude-code',
          content: mockMode === 'headings_only'
            ? 'Generic knowledge item without expected string'
            : 'Seeded Acceptance Test Knowledge Document Content',
        });
      }

      res.end(JSON.stringify({ ok: true, results }));
      return;
    }

    res.writeHead(404, { 'content-type': 'application/json' });
    res.end(JSON.stringify({ ok: false, error: 'Not found' }));
  });
});

const port = await listen(server);
const url = `http://127.0.0.1:${port}`;

try {
  const commonArgs = [
    '--expected-memory', 'Seeded Acceptance Test Memory Record Content',
    '--expected-knowledge', 'Seeded Acceptance Test Knowledge Document Content',
  ];

  // 1. Both expected records present -> Exit 0
  mockMode = 'both_present';
  const resBoth = await runAcceptanceScript({ url, args: commonArgs });
  assert.equal(resBoth.code, 0, `Expected exit 0, got ${resBoth.code}. stderr: ${resBoth.stderr}`);
  assert.match(resBoth.stdout, /ACCEPTANCE SUCCESS \(Exit 0\)/);

  // 2. Memory present, Knowledge missing -> Exit 3
  mockMode = 'memory_only';
  const resMemOnly = await runAcceptanceScript({ url, args: commonArgs });
  assert.equal(resMemOnly.code, 3, `Expected exit 3, got ${resMemOnly.code}`);
  assert.match(resMemOnly.stdout, /ACCEPTANCE PARTIAL \(Exit 3\)/);

  // 3. Knowledge present, Memory missing -> Exit 3
  mockMode = 'knowledge_only';
  const resKnowOnly = await runAcceptanceScript({ url, args: commonArgs });
  assert.equal(resKnowOnly.code, 3, `Expected exit 3, got ${resKnowOnly.code}`);
  assert.match(resKnowOnly.stdout, /ACCEPTANCE PARTIAL \(Exit 3\)/);

  // 4. Neither expected record present -> Exit 3
  mockMode = 'neither_present';
  const resNeither = await runAcceptanceScript({ url, args: commonArgs });
  assert.equal(resNeither.code, 3, `Expected exit 3, got ${resNeither.code}`);

  // 5. Generic section headings without expected record content -> Exit 3
  mockMode = 'headings_only';
  const resHeadingsOnly = await runAcceptanceScript({ url, args: commonArgs });
  assert.equal(resHeadingsOnly.code, 3, `Expected exit 3, got ${resHeadingsOnly.code}`);

  // 6. Memory service unavailable -> Exit 1
  mockMode = 'service_unavailable';
  const resUnavail = await runAcceptanceScript({ url, args: commonArgs });
  assert.equal(resUnavail.code, 1, `Expected exit 1, got ${resUnavail.code}`);

  // 7. Malformed hook output / execution failure -> Exit 2
  mockMode = 'both_present';
  const badHookDir = await mkdtemp(path.join(os.tmpdir(), 'ce-bad-hook-'));
  const badHookFile = path.join(badHookDir, 'bad-hook.js');
  await writeFile(badHookFile, 'console.log("NOT_JSON_OUTPUT");');
  try {
    const resMalformed = await runAcceptanceScript({
      url,
      args: commonArgs,
      env: { FLOSSWARE_HOOK_PATH: badHookFile },
    });
    assert.equal(resMalformed.code, 2, `Expected exit 2 for malformed response, got ${resMalformed.code}`);
  } finally {
    await rm(badHookDir, { recursive: true, force: true });
  }

  // 8. Invocation from a working directory outside the repository root
  mockMode = 'both_present';
  const tmpDir = await mkdtemp(path.join(os.tmpdir(), 'ce-acceptance-outside-repo-'));
  try {
    const resOutside = await runAcceptanceScript({ url, args: commonArgs, cwd: tmpDir });
    assert.equal(resOutside.code, 0, `Expected exit 0 from outside cwd, got ${resOutside.code}. stderr: ${resOutside.stderr}`);
  } finally {
    await rm(tmpDir, { recursive: true, force: true });
  }

  // 9. Missing installed hook fails clearly instead of falling back to repo source
  const missingProfile = await mkdtemp(path.join(os.tmpdir(), 'ce-missing-installed-hook-'));
  try {
    const resMissingHook = await runAcceptanceScript({
      url,
      args: commonArgs,
      env: {
        FLOSSWARE_HOOK_PATH: '',
        ENSEMBLE_CLAUDE_CODE_CONFIG_DIR: missingProfile,
      },
    });
    assert.equal(resMissingHook.code, 2, `Expected exit 2 for missing installed hook, got ${resMissingHook.code}`);
    assert.match(resMissingHook.stderr, /Installed hook script not found/);
    assert.match(resMissingHook.stderr, /managed profile/);
  } finally {
    await rm(missingProfile, { recursive: true, force: true });
  }

  console.log('ALL verify-memory-hook-acceptance regression tests passed successfully!');
} finally {
  await close(server);
}
