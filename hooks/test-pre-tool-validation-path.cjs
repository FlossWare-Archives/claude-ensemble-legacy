// Regression test for the configurable pre-tool validator path.

const assert = require('node:assert/strict');
const Module = require('node:module');
const path = require('node:path');

const originalLoad = Module._load;
const originalValidatorPath = process.env.CE_PRE_TOOL_VALIDATOR_PATH;
const originalProjectDir = process.env.CLAUDE_PROJECT_DIR;
const configuredPath = path.join(require('node:os').tmpdir(), 'ce-test-validator.py');
let capturedArgs;

Module._load = function(request, parent, isMain) {
  if (request === 'child_process') {
    return {
      ...originalLoad.call(this, request, parent, isMain),
      execFileSync: (_command, args) => {
        capturedArgs = args;
        return JSON.stringify({ valid: true, warnings: [] });
      }
    };
  }
  return originalLoad.call(this, request, parent, isMain);
};

(async () => {
  try {
    process.env.CE_PRE_TOOL_VALIDATOR_PATH = configuredPath;
    process.env.CLAUDE_PROJECT_DIR = '/unrelated/project';
    const hook = require('./pre-tool-validation.cjs');
    await hook({ tool: 'Bash', parameters: { command: 'echo safe' } });
    assert.equal(capturedArgs[0], configuredPath);
  } finally {
    Module._load = originalLoad;
    if (originalValidatorPath === undefined) delete process.env.CE_PRE_TOOL_VALIDATOR_PATH;
    else process.env.CE_PRE_TOOL_VALIDATOR_PATH = originalValidatorPath;
    if (originalProjectDir === undefined) delete process.env.CLAUDE_PROJECT_DIR;
    else process.env.CLAUDE_PROJECT_DIR = originalProjectDir;
  }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
