#!/usr/bin/env node
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
const __dirname = path.dirname(fileURLToPath(import.meta.url));

async function runHook(config) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'ce-rag-config-'));
  const configPath = path.join(directory, 'rag.json');
  fs.writeFileSync(configPath, JSON.stringify(config));
  const hookPath = path.join(__dirname, 'memory-search-on-prompt.js');
  const child = spawn(process.execPath, [hookPath], {
    env: {
      ...process.env,
      FLOSSWARE_CE_RAG_CONFIG: configPath,
      FLOSSWARE_CE_HOOK_DIAGNOSTICS: '1',
      FLOSSWARE_MEMORY_URL: 'http://127.0.0.1:1',
      FLOSSWARE_GRAPH_URL: 'http://127.0.0.1:1',
    },
    stdio: ['pipe', 'pipe', 'pipe'],
  });
  let stdout = '';
  let stderr = '';
  child.stdout.setEncoding('utf8').on('data', chunk => { stdout += chunk; });
  child.stderr.setEncoding('utf8').on('data', chunk => { stderr += chunk; });
  child.stdin.end(JSON.stringify({ prompt: 'Explain CE prompt retrieval behavior', cwd: '/tmp/example-project' }));
  try {
    const exitCode = await new Promise((resolve, reject) => {
      child.once('error', reject);
      child.once('close', resolve);
    });
    return { exitCode, stdout, stderr };
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
}

test('disabled RAG sources are not called and report disabled status', async () => {
  const result = await runHook({
    memory: { enabled: false },
    knowledge: { enabled: false },
    graph: { enabled: false },
  });
  assert.equal(result.exitCode, 0, result.stderr);
  assert.equal(result.stdout, '');
  assert.doesNotMatch(result.stderr, /search unavailable|status unavailable|Graph service returned/);
  const line = result.stderr.split('\n').filter(value => value.startsWith('CE_HOOK_DIAGNOSTICS ')).at(-1);
  assert.ok(line, result.stderr);
  const diagnostics = JSON.parse(line.slice('CE_HOOK_DIAGNOSTICS '.length));
  assert.equal(diagnostics.memoryStatus, 'disabled');
  assert.equal(diagnostics.knowledgeStatus, 'disabled');
  assert.equal(diagnostics.knowledgeIndexStatus, 'disabled');
  assert.equal(diagnostics.graphStatus, 'disabled');
});

test('invalid RAG config fails closed without invoking services', async () => {
  const result = await runHook({ memory: { enabled: 'sometimes' } });
  assert.equal(result.exitCode, 0, result.stderr);
  assert.equal(result.stdout, '');
  assert.match(result.stderr, /RAG configuration invalid: memory.enabled must be boolean/);
  assert.doesNotMatch(result.stderr, /search unavailable|status unavailable|Graph service returned/);
});
