#!/usr/bin/env node

/**
 * Operational acceptance verification script for Claude Ensemble Memory REST integration.
 *
 * Verifies that the production UserPromptSubmit hook (hooks/memory-search-on-prompt.js)
 * communicates with the configured Memory REST endpoint and retrieves identifiable Memory
 * and Knowledge records into Claude Code prompt context.
 *
 * Usage:
 *   node scripts/verify-memory-hook-acceptance.cjs [prompt] [expectedMemoryText] [expectedKnowledgeText]
 *
 * Environment variables:
 *   FLOSSWARE_MEMORY_URL / ENSEMBLE_MEMORY_URL - Configured Memory REST base URL (default: http://127.0.0.1:8767)
 *   EXPECTED_MEMORY_TEXT - Substring to identify seeded Memory record (default: "decision")
 *   EXPECTED_KNOWLEDGE_TEXT - Substring to identify seeded Knowledge document (default: "Claude Code")
 *
 * Exit status codes:
 *   0 - Success: Both expected Memory and Knowledge records were retrieved and injected into prompt context.
 *   1 - Service Unavailable: Memory REST service is not reachable or healthy.
 *   2 - Contract/Execution Error: Hook process error, non-zero exit code, or malformed JSON payload.
 *   3 - Missing/Partial Results: Expected Memory or Knowledge record content missing from hook context.
 */

const fs = require('fs');
const http = require('http');
const path = require('path');
const { spawn } = require('child_process');

const repoRoot = path.resolve(__dirname, '..');
const hookPath = path.join(repoRoot, 'hooks', 'memory-search-on-prompt.js');

const memoryUrl = process.env.FLOSSWARE_MEMORY_URL || process.env.ENSEMBLE_MEMORY_URL || 'http://127.0.0.1:8767';
const queryPrompt = process.argv[2] || process.env.EXPECTED_PROMPT || 'architecture decision';
const expectedMemoryText = process.argv[3] || process.env.EXPECTED_MEMORY_TEXT || 'decision';
const expectedKnowledgeText = process.argv[4] || process.env.EXPECTED_KNOWLEDGE_TEXT || 'Claude Code';

if (!fs.existsSync(hookPath)) {
  console.error(`ACCEPTANCE ERROR (Exit 2): Hook script not found at relative path: ${hookPath}`);
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
    console.error('  Remediation: Start memory service via tools/ce dev run memory or check systemctl --user status claude-memory.service.');
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
    if (hookResult.stderr) console.error('stderr:', hookResult.stderr);
    process.exit(2);
  }

  if (hookResult.stderr.includes('Memory search unavailable:')) {
    console.error(`ACCEPTANCE FAILURE (Exit 1): Hook reported Memory search unavailable: ${hookResult.stderr}`);
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
    console.error('stdout:', hookResult.stdout);
    process.exit(2);
  }

  const hookOutput = payload?.hookSpecificOutput;
  if (!hookOutput || hookOutput.hookEventName !== 'UserPromptSubmit') {
    console.error('ACCEPTANCE FAILURE (Exit 2): Response payload missing valid hookSpecificOutput metadata.');
    process.exit(2);
  }

  const additionalContext = hookOutput.additionalContext;
  if (typeof additionalContext !== 'string' || !additionalContext.trim()) {
    console.error('ACCEPTANCE FAILURE (Exit 3): additionalContext was missing or empty.');
    process.exit(3);
  }

  const contextRetrieval = hookOutput.contextRetrieval || {};
  const memoryCount = typeof contextRetrieval.memoryCount === 'number' ? contextRetrieval.memoryCount : 0;
  const knowledgeCount = typeof contextRetrieval.knowledgeCount === 'number' ? contextRetrieval.knowledgeCount : 0;

  // Extract memory and knowledge sections from formatted additionalContext
  const memorySectionMatch = additionalContext.match(/### Relevant Memory \(what happened\)\n([\s\S]*?)(?=\n###|\n?$)/);
  const knowledgeSectionMatch = additionalContext.match(/### Relevant Knowledge \(what is currently known\)\n([\s\S]*?)(?=\n###|\n?$)/);

  const memorySectionContent = memorySectionMatch ? memorySectionMatch[1] : '';
  const knowledgeSectionContent = knowledgeSectionMatch ? knowledgeSectionMatch[1] : '';

  const hasMemoryRecord = memorySectionContent.toLowerCase().includes(expectedMemoryText.toLowerCase());
  const hasKnowledgeRecord = knowledgeSectionContent.toLowerCase().includes(expectedKnowledgeText.toLowerCase());

  console.log(`ACCEPTANCE CHECK: Searching context for Memory="${expectedMemoryText}" and Knowledge="${expectedKnowledgeText}"`);
  console.log(`ACCEPTANCE RESULT: Memory found=${hasMemoryRecord} (count=${memoryCount}), Knowledge found=${hasKnowledgeRecord} (count=${knowledgeCount})`);

  if (hasMemoryRecord && hasKnowledgeRecord) {
    console.log('ACCEPTANCE SUCCESS (Exit 0): Both expected Memory and Knowledge records were verified in the prompt context.');
    process.exit(0);
  }

  if (!hasMemoryRecord && !hasKnowledgeRecord) {
    console.error(`ACCEPTANCE FAILURE (Exit 3): Neither expected Memory ("${expectedMemoryText}") nor Knowledge ("${expectedKnowledgeText}") record content was found in context.`);
    process.exit(3);
  }

  if (!hasMemoryRecord) {
    console.error(`ACCEPTANCE FAILURE (Exit 3): Expected Memory record content ("${expectedMemoryText}") was missing from context.`);
    process.exit(3);
  }

  if (!hasKnowledgeRecord) {
    console.error(`ACCEPTANCE FAILURE (Exit 3): Expected Knowledge record content ("${expectedKnowledgeText}") was missing from context.`);
    process.exit(3);
  }
}

main().catch(err => {
  console.error(`ACCEPTANCE UNHANDLED ERROR (Exit 2): ${err.message}`);
  process.exit(2);
});
