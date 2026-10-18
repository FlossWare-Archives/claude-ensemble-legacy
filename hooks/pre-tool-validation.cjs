#!/usr/bin/env node
/**
 * Pre-tool validation hook (ECC #235)
 * Automatically validates ALL tool calls before execution
 *
 * Validates:
 * - Bash commands (dangerous patterns, protected paths)
 * - File operations (permissions, protected paths)
 * - Parameter schemas
 */

const { execFileSync } = require('child_process');
const path = require('path');

module.exports = async function preToolValidation(context) {
  const { tool, parameters } = context;

  // Only validate these tools
  const validatedTools = ['Bash', 'Read', 'Write', 'Edit'];

  if (!validatedTools.includes(tool)) {
    return { allowed: true };
  }

  try {
    // Build JSON request for validator
    const request = JSON.stringify({
      tool_name: tool,
      parameters: parameters,
      dry_run: false,
      permission_check: true
    });

    // Call pre_tool_validator.py with JSON via stdin
    const configuredValidatorPath = process.env.CE_PRE_TOOL_VALIDATOR_PATH;
    const projectDir = process.env.CLAUDE_PROJECT_DIR || path.join(process.env.HOME, 'Development/redhat/scm/gitlab/cee/sfloess/claude-global-skills');
    // CE_PRE_TOOL_VALIDATOR_PATH overrides the legacy project-relative default.
    const validatorPath = configuredValidatorPath || path.join(projectDir, 'shared/pre_tool_validator.py');

    const result = execFileSync('python3', [validatorPath], {
      input: request,
      encoding: 'utf8',
      cwd: projectDir
    });

    const validation = JSON.parse(result);

    if (!validation.valid) {
      console.error(`[PRE-TOOL VALIDATION] ❌ BLOCKED: ${tool}`);
      console.error(`[PRE-TOOL VALIDATION]    Errors: ${validation.errors.join(', ')}`);

      throw new Error(`Pre-tool validation failed: ${validation.errors.join('; ')}`);
    }

    // Log warnings
    if (validation.warnings && validation.warnings.length > 0) {
      console.warn(`[PRE-TOOL VALIDATION] ⚠️  ${tool} warnings:`);
      validation.warnings.forEach(w => console.warn(`     - ${w}`));
    }

    // Log safe execution
    const cmdPreview = parameters.command ? parameters.command.substring(0, 50) + '...' :
                      parameters.file_path ? parameters.file_path :
                      tool;
    console.log(`[PRE-TOOL VALIDATION] ✅ SAFE: ${cmdPreview}`);

    return { allowed: true };

  } catch (err) {
    if (err.message.includes('Pre-tool validation failed')) {
      throw err;
    }

    // Validator error - allow by default (fail open for availability)
    console.warn(`[PRE-TOOL VALIDATION] ⚠️  Validator error, allowing: ${err.message}`);
    return { allowed: true };
  }
};
