#!/usr/bin/env node

/**
 * Operational acceptance verification script for Claude Ensemble Memory REST integration.
 *
 * Verifies that the production UserPromptSubmit hook (hooks/memory-search-on-prompt.js)
 * communicates with the configured Memory REST endpoint and emits both Memory and Knowledge
 * records into the prompt context.
 *
 * Exit status codes:
 *   0 - Success: Both Memory and Knowledge records retrieved and injected into prompt context.
 *   1 - Service Unavailable: Memory REST service is not reachable.
 *   2 - Contract/Execution Error: Hook execution failed, malformed output, or invalid payload.
 *   3 - Missing/Partial Context: Hook returned no results or was missing required Memory/Knowledge entries.
 */

const fs = require('fs');
const http = require('http');
const path = require('path');
const { spawn } = require('child_process');

const repoRoot = path.resolve(__dirname, '..');
const hookPath = path.join(repoRoot, 'hooks', 'memory-search-on-prompt.js');

const memoryUrl = process.env.FLOSSWARE_MEMORY_URL || process.env.ENSEMBLE_MEMORY_URL || 'http://127.0.0.1:8767';
const queryPrompt = process.argv[2] || 'architecture decision';

if (!fs.existsSync(hookPath)) {
  console.error(`ACCEPTANCE ERROR: Hook script not found at ${hookPath}`);
  process.exit(2);
}

function checkServiceHealth(urlStr) {
  return new Promise((resolve) => {
    try {
      const url = new URL('/health', urlStr.endsWith('/') ? urlStr : `${urlStr}/`);
      const req = http.get(url, { timeout: 2000 }, (res) => {
        let body = '';
        res.on('data', chunk => { body += chunk; });
        res.on('end', () => {
          if (res.statusCode === 200) {
            try {
              const payload = JSON.parse(body);
              resolve(payload && payload.ok === true);
            } catch {
              resolve(false);
            }
          } else {
            resolve(false);
          }
        });
      });
      req.on('error', () => resolve(false));
      req.on('timeout', () => { req.destroy(); resolve(false); });
    } catch {
      resolve(false);
    }
  });
}

function runHook(urlStr, promptText) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [hookPath], {
      cwd: repoRoot,
      env: {
        ...process.env,
        FLOSSWARE_MEMORY_URL: urlStr,
        FLOSSWARE_CE_HOOK_DIAGNOSTICS: '1',
      },
      stdio: ['pipe', 'pipe', 'pipe'],
    });

    let stdout = '';
    let stderr = '';
    child.stdout.on('data', chunk => { stdout += chunk; });
    child.stderr.on('data', chunk => { stderr += chunk; });
    child.on('error', reject);

    child.on('close', code => resolve({ code, stdout, stderr }));

    const eventPayload = JSON.stringify({
      hook_event_name: 'UserPromptSubmit',
      prompt: promptText,
    });
    child.stdin.end(eventPayload);
  });
}

async function main() {
  const isHealthy = await checkServiceHealth(memoryUrl);
  if (!isHealthy) {
    console.error(`ACCEPTANCE FAILURE (Exit 1): Memory REST service at ${memoryUrl} is unavailable or unhealthy.`);
    console.error('  Remediation: Ensure claude-memory.service is running or start it via tools/ce dev run memory.');
    process.exit(1);
  }

  let hookResult;
  try {
    hookResult = await runHook(memoryUrl, queryPrompt);
  } catch (err) {
    console.error(`ACCEPTANCE FAILURE (Exit 2): Failed to execute hook script: ${err.message}`);
    process.exit(2);
  }

  if (hookResult.code !== 0) {
    console.error(`ACCEPTANCE FAILURE (Exit 2): Hook process exited with non-zero code ${hookResult.code}`);
    console.error('stderr output:', hookResult.stderr);
    process.exit(2);
  }

  if (hookResult.stderr.includes('Memory search unavailable:')) {
    console.error(`ACCEPTANCE FAILURE (Exit 1): Hook reported Memory search unavailable.`);
    console.error(hookResult.stderr);
    process.exit(1);
  }

  if (!hookResult.stdout.trim()) {
    console.error('ACCEPTANCE FAILURE (Exit 3): Hook produced empty stdout; no context emitted.');
    process.exit(3);
  }

  let payload;
  try {
    payload = JSON.parse(hookResult.stdout);
  } catch (err) {
    console.error(`ACCEPTANCE FAILURE (Exit 2): Hook output was not valid JSON: ${err.message}`);
    console.error('stdout output:', hookResult.stdout);
    process.exit(2);
  }

  const hookOutput = payload?.hookSpecificOutput;
  if (!hookOutput || hookOutput.hookEventName !== 'UserPromptSubmit') {
    console.error('ACCEPTANCE FAILURE (Exit 2): Response payload missing valid hookSpecificOutput metadata.');
    process.exit(2);
  }

  const additionalContext = hookOutput.additionalContext || '';
  const contextRetrieval = hookOutput.contextRetrieval || {};

  if (!additionalContext.trim()) {
    console.error('ACCEPTANCE FAILURE (Exit 3): additionalContext was empty.');
    process.exit(3);
  }

  const memoryCount = contextRetrieval.memoryCount || 0;
  const knowledgeCount = contextRetrieval.knowledgeCount || 0;
  const hasMemorySection = additionalContext.includes('### Relevant Memory');
  const hasKnowledgeSection = additionalContext.includes('### Relevant Knowledge');

  console.log(`ACCEPTANCE METRICS: Memory records=${memoryCount}, Knowledge records=${knowledgeCount}`);

  if ((memoryCount > 0 || hasMemorySection) && (knowledgeCount > 0 || hasKnowledgeSection)) {
    console.log('ACCEPTANCE SUCCESS (Exit 0): Both Memory and Knowledge records were retrieved and injected into prompt context.');
    console.log('\n--- Injected Prompt Context ---\n' + additionalContext + '\n-------------------------------\n');
    process.exit(0);
  } else if (memoryCount > 0 || hasMemorySection) {
    console.log('ACCEPTANCE PARTIAL (Exit 3): Memory records retrieved, but no Knowledge documents found.');
    console.log('\n--- Injected Prompt Context ---\n' + additionalContext + '\n-------------------------------\n');
    process.exit(3);
  } else if (knowledgeCount > 0 || hasKnowledgeSection) {
    console.log('ACCEPTANCE PARTIAL (Exit 3): Knowledge records retrieved, but no Memory entries found.');
    console.log('\n--- Injected Prompt Context ---\n' + additionalContext + '\n-------------------------------\n');
    process.exit(3);
  } else {
    console.error('ACCEPTANCE FAILURE (Exit 3): Neither Memory nor Knowledge records were found in the context.');
    process.exit(3);
  }
}

main().catch(err => {
  console.error(`ACCEPTANCE UNHANDLED ERROR (Exit 2): ${err.message}`);
  process.exit(2);
});
