#!/usr/bin/env node

/**
 * Operational acceptance verification script for Claude Ensemble Memory REST integration.
 *
 * Verifies that the installed UserPromptSubmit hook communicates with the configured
 * Memory REST endpoint and injects identifiable Memory and Knowledge records into
 * the prompt context. FLOSSWARE_HOOK_PATH can select a deliberate test fixture.
 *
 * Exit status codes:
 *   0 - Success: Both expected Memory and Knowledge records retrieved and injected into prompt context.
 *   1 - Service Unavailable: Memory REST service is not reachable or healthy.
 *   2 - Contract/Execution Error: Hook execution failed, malformed output, or invalid payload schema.
 *   3 - Missing/Partial Context: Context was missing one or both expected Memory and Knowledge records.
 */

const fs = require('fs');
const http = require('http');
const path = require('path');
const os = require('os');
const { spawn } = require('child_process');

const repoRoot = path.resolve(__dirname, '..');
const profileDir = process.env.ENSEMBLE_CLAUDE_CODE_CONFIG_DIR
  || process.env.CLAUDE_CONFIG_DIR
  || path.join(os.homedir(), '.FlossWare', 'claude-ensemble', 'shared', 'claude-code');
// Acceptance must exercise the installed hook, not silently fall back to the
// repository copy and accidentally pass when installation is broken.
const hookPath = process.env.FLOSSWARE_HOOK_PATH || path.join(profileDir, 'hooks', 'memory-search-on-prompt.js');

const memoryUrl = process.env.FLOSSWARE_MEMORY_URL || process.env.ENSEMBLE_MEMORY_URL || 'http://127.0.0.1:8767';

let queryPrompt = 'architecture decision';
let expectedMemory = process.env.EXPECTED_MEMORY_CONTENT || 'Acceptance Test Memory';
let expectedKnowledge = process.env.EXPECTED_KNOWLEDGE_CONTENT || 'Acceptance Test Knowledge';

const args = process.argv.slice(2);
for (let i = 0; i < args.length; i++) {
  const arg = args[i];
  if ((arg === '--expected-memory' || arg === '-m') && i + 1 < args.length) {
    expectedMemory = args[++i];
  } else if ((arg === '--expected-knowledge' || arg === '-k') && i + 1 < args.length) {
    expectedKnowledge = args[++i];
  } else if (arg === '--prompt' && i + 1 < args.length) {
    queryPrompt = args[++i];
  } else if (!arg.startsWith('-') && i === 0) {
    queryPrompt = arg;
  }
}

if (!fs.existsSync(hookPath)) {
  console.error(`ACCEPTANCE ERROR (Exit 2): Installed hook script not found at ${hookPath}`);
  console.error('  Remediation: install Claude Ensemble into the managed profile or set FLOSSWARE_HOOK_PATH explicitly for a deliberate test.');
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
    console.error('  Remediation: Ensure Memory service is running or start it via tools/ce dev run memory.');
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

  const additionalContext = typeof hookOutput.additionalContext === 'string' ? hookOutput.additionalContext : '';

  const hasExpectedMemory = Boolean(expectedMemory && additionalContext.includes(expectedMemory));
  const hasExpectedKnowledge = Boolean(expectedKnowledge && additionalContext.includes(expectedKnowledge));

  console.log(`ACCEPTANCE METRICS: Memory expected="${expectedMemory}" (found=${hasExpectedMemory}), Knowledge expected="${expectedKnowledge}" (found=${hasExpectedKnowledge})`);

  if (hasExpectedMemory && hasExpectedKnowledge) {
    console.log('ACCEPTANCE SUCCESS (Exit 0): Both expected Memory and Knowledge records were retrieved and injected into prompt context.');
    console.log('\n--- Injected Prompt Context ---\n' + additionalContext + '\n-------------------------------\n');
    process.exit(0);
  } else if (hasExpectedMemory) {
    console.log('ACCEPTANCE PARTIAL (Exit 3): Expected Memory record retrieved, but expected Knowledge document was missing.');
    console.log('\n--- Injected Prompt Context ---\n' + additionalContext + '\n-------------------------------\n');
    process.exit(3);
  } else if (hasExpectedKnowledge) {
    console.log('ACCEPTANCE PARTIAL (Exit 3): Expected Knowledge document retrieved, but expected Memory record was missing.');
    console.log('\n--- Injected Prompt Context ---\n' + additionalContext + '\n-------------------------------\n');
    process.exit(3);
  } else {
    console.error('ACCEPTANCE FAILURE (Exit 3): Neither expected Memory nor Knowledge record was found in the context.');
    process.exit(3);
  }
}

main().catch(err => {
  console.error(`ACCEPTANCE UNHANDLED ERROR (Exit 2): ${err.message}`);
  process.exit(2);
});
