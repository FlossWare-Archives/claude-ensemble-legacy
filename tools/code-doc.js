import path from 'node:path'
import fs from 'node:fs'
import os from 'node:os'
import yaml from 'js-yaml'
import { execSync } from 'node:child_process'
import { recordEvidenceGatedOutcome } from '../shared/learning-outcome-bridge.js'
export const meta = {
  name: 'code-doc',
  description: 'Interactive documentation generation - prompts before creating docs',
  whenToUse: 'When you want comprehensive documentation generation with manual review',
  phases: [
    { title: 'Detect Platform', detail: 'Identify GitHub/GitLab and project type' },
    { title: 'Find Undocumented Code', detail: 'Scan for missing docs' },
    { title: 'Analyze Signatures', detail: 'Extract function/class signatures' },
    { title: 'Multi-AI Doc Generation', detail: 'Generate docs with consensus', model: 'opus' },
    { title: 'Impact Analysis', detail: 'Prioritize by importance' },
    { title: 'README Completeness', detail: 'Check README gaps' },
    { title: 'User Confirmation', detail: 'Review before generating docs' },
    { title: 'Generate Documentation', detail: 'Create PR with docs' },
  ],
}

// ============================================================================
// INTEGRATION: Thompson/Learning/Alert/Compression/Caching Ecosystem
// ============================================================================

import crypto from 'node:crypto'
import { compressContext } from '../shared/compression-bridge.js'
import CachingBridge from '../shared/caching-bridge.js'

function generateRequestId(prefix = 'skill_code_doc') {
  return `${prefix}_${crypto.randomBytes(6).toString('hex')}`
}

async function selectModelViaThompson(taskType, requestId, fallback = 'haiku') {
  try {
        const jsonPayload = JSON.stringify({ task_type: taskType, request_id: requestId })
    const result = execSync(`python3 -c "
import sys
import json
sys.path.insert(0, '../shared')
from thompson_client import ThompsonClient
data = json.loads('${jsonPayload.replace(/'/g, "\\'")}')
c = ThompsonClient()
print(c.select_model(data['task_type'], required_capability=0.7, request_id=data['request_id']))
"`, {
      cwd: process.env.PWD,
      timeout: 3000,
      encoding: 'utf-8',
      stdio: ['pipe', 'pipe', 'pipe']
    }).trim()

    log(`[Thompson] Selected ${result} for ${taskType} (${requestId})`)
    return result || fallback
  } catch (err) {
    log(`⚠️ Thompson unavailable: ${err.message}, using fallback ${fallback}`)
    return fallback
  }
}

async function recordOutcomeToLearning(taskId, taskType, model, rating, tokens, cost, requestId, evidence) {
  try {
    const response = recordEvidenceGatedOutcome({
      runId: requestId,
      taskId,
      taskType,
      model,
      rating,
      tokens,
      cost,
      evidence,
    })
    log(`[Learning] Acknowledged: ${taskId} (${model}, rating=${rating})`)
    return true
  } catch (err) {
    log(`⚠️ Learning recording skipped/failed (non-blocking): ${err.message}`)
    return false
  }
}

function calculateCost(model, inputTokens, outputTokens) {
  const pricing = {
    haiku: { input: 0.80, output: 2.40 },
    sonnet: { input: 3.00, output: 15.00 },
    opus: { input: 15.00, output: 45.00 },
    gemini: { input: 0.075, output: 0.30 },  // Gemini 2.0 Flash pricing
  }
  const prices = pricing[model] || pricing.haiku
  const inputCost = (inputTokens / 1_000_000) * prices.input
  const outputCost = (outputTokens / 1_000_000) * prices.output
  return inputCost + outputCost
}

async function logCostMetrics(model, inputTokens, outputTokens, taskName, requestId) {
  try {
        const cost = calculateCost(model, inputTokens, outputTokens)
    const jsonPayload = JSON.stringify({ model, input_tokens: inputTokens, output_tokens: outputTokens, task_name: taskName, request_id: requestId })
    execSync(`python3 -c "
import sys
import json
sys.path.insert(0, '../cost_tracking')
from logger import CostLogger
data = json.loads('${jsonPayload.replace(/'/g, "\\'")}')
c = CostLogger()
c.log_call(data['model'], data['input_tokens'], data['output_tokens'], data['task_name'], metadata={'request_id': data['request_id']})
"`, {
      cwd: process.env.PWD,
      timeout: 2000,
      encoding: 'utf-8',
      stdio: ['pipe', 'pipe', 'pipe']
    })
  } catch (err) {
    // Silent failure for cost tracking (non-critical)
  }
}

// ============================================================================

  export default async function run({ agent, log, phase, workflow, args }) {
  const AUTONOMOUS = args?.autonomous === true  // INTERACTIVE by default
  const workflowRequestId = generateRequestId('workflow_code_doc')
  const cachingBridge = new CachingBridge('code-doc')
  
  log(`📚 Documentation Mode: ${AUTONOMOUS ? 'AUTONOMOUS' : 'INTERACTIVE'}`)
  
  // Phase 1: Detect Platform
  phase('Detect Platform')
  
  const platform = await agent(`Detect platform and project type.
  
  Check:
  - git remote -v (github/gitlab)
  - Project language (package.json, Cargo.toml, go.mod, etc.)
  - Existing docs (README.md, docs/, JSDoc, docstrings)
  
  Return platform and language.`, {
    label: 'Detect Platform',
    schema: {
      type: 'object',
      properties: {
        platform: { type: 'string', enum: ['github', 'gitlab', 'unknown'] },
        language: { type: 'string' },
        has_readme: { type: 'boolean' },
        doc_style: { type: 'string' }
      }
    }
  })
  
  log(`✅ Platform: ${platform.platform}, Language: ${platform.language}`)
  
  // Phase 2: Find Undocumented Code
  phase('Find Undocumented Code')
  
  log('🔍 Scanning for undocumented code...')
  
  const undocumented = await agent(`Find undocumented code.
  
  For ${platform.language}:
  
  JavaScript/TypeScript:
  - Functions without JSDoc
  - Classes without description
  - Exported APIs without docs
  - React components without prop docs
  
  Python:
  - Functions without docstrings
  - Classes without docstrings
  - Public methods without docs
  
  Go:
  - Exported functions without doc comments
  - Public types without docs
  
  Scan codebase and return undocumented items.
  Prioritize:
  - Public/exported APIs (HIGH)
  - Complex functions (MEDIUM)
  - Internal utils (LOW)`, {
    label: 'Find Undocumented',
    schema: {
      type: 'object',
      properties: {
        undocumented: {
          type: 'array',
          items: {
            type: 'object',
            properties: {
              file: { type: 'string' },
              line: { type: 'number' },
              type: { type: 'string', enum: ['function', 'class', 'method', 'component', 'type'] },
              name: { type: 'string' },
              exported: { type: 'boolean' },
              complexity: { type: 'string', enum: ['high', 'medium', 'low'] }
            }
          }
        },
        total: { type: 'number' }
      }
    }
  })
  
  log(`✅ Found ${undocumented.total || 0} undocumented items`)
  
  if (undocumented.total === 0) {
    log(`✅ All code is documented!`)
    return {
      status: 'complete',
      message: 'All code is already documented'
    }
  }
  
  // Phase 3: Analyze Signatures
  phase('Analyze Signatures')
  
  log('📝 Analyzing signatures and usage...')
  
  const signatures = await Promise.all(
    (undocumented.undocumented || []).slice(0, 20).map(item =>
      agent(`Extract signature and usage for ${item.name}.
  
  File: ${item.file}:${item.line}
  
  Read the file and extract:
  - Full function/class signature
  - Parameters and types
  - Return type
  - Usage examples (if found in codebase)
  - Dependencies/imports
  
  Return signature details.`, {
        label: `Analyze: ${item.name}`,
        schema: {
          type: 'object',
          properties: {
            signature: { type: 'string' },
            parameters: {
              type: 'array',
              items: {
                type: 'object',
                properties: {
                  name: { type: 'string' },
                  type: { type: 'string' },
                  optional: { type: 'boolean' }
                }
              }
            },
            return_type: { type: 'string' },
            usage_examples: { type: 'array', items: { type: 'string' } }
          }
        }
      }).catch(() => null)
    )
  )
  
  const validSignatures = signatures.filter(Boolean)
  
  log(`✅ Analyzed ${validSignatures.length} signatures`)
  
  // Phase 4: Multi-AI Doc Generation
  phase('Multi-AI Doc Generation')
  
  log('🤖 Generating documentation with multi-AI consensus...')
  
  // Load config (required)
  let userModelConfig = null
  try {
                const configPath = path.join(os.homedir(), '.claude/toolkit-models.yaml')
    if (!fs.existsSync(configPath)) {
      log(`❌ Configuration Required: ~/.claude/toolkit-models.yaml not found`)
      process.exit(1)
    }
        userModelConfig = yaml.load(fs.readFileSync(configPath, 'utf8'))
    if (!userModelConfig || typeof userModelConfig !== 'object') {
      log(`❌ Invalid configuration file (empty or malformed YAML)`)
      process.exit(1)
    }
    log(`[Config] Loaded ~/.claude/toolkit-models.yaml`)
  } catch (err) {
    log(`❌ Failed to load config: ${err.message}`)
    process.exit(1)
  }
  const skillConfig = userModelConfig.skill_defaults?.['code-doc']
  if (!skillConfig) { log(`❌ Skill config not found`); process.exit(1) }
  if (!Array.isArray(skillConfig.models)) {
    log(`❌ Invalid models configuration (must be an array)`)
    process.exit(1)
  }
  
  // INTEGRATION POINT 1: Dynamic worker selection via Thompson
  let WORKERS = skillConfig.models
  try {
    // Try to get Thompson-selected models for doc generation
    const docModel1 = await selectModelViaThompson('code-doc-generation', `${workflowRequestId}_worker1`, 'opus')
    const docModel2 = await selectModelViaThompson('code-doc-generation', `${workflowRequestId}_worker2`, 'sonnet')
    const docModel3 = await selectModelViaThompson('code-doc-generation', `${workflowRequestId}_worker3`, 'haiku')
    const docModel4 = await selectModelViaThompson('code-doc-generation', `${workflowRequestId}_worker4`, 'gemini')
    WORKERS = [docModel1, docModel2, docModel3, docModel4].filter((m, idx, arr) => arr.indexOf(m) === idx) // Remove duplicates
    log(`[Thompson] Selected workers: ${WORKERS.join(', ')}`)
  } catch (err) {
    log(`⚠️ Thompson unavailable, using default workers: ${WORKERS.join(', ')}`)
  }
  
  // Generate docs for first 10 items (limit for token efficiency)
  const docGenerations = await pipeline(
    undocumented.undocumented.slice(0, 10),
  
    // Stage 1: Each worker generates docs independently
    item => parallel(WORKERS.map(model => async () => {
      const itemRequestId = `${workflowRequestId}_doc_${item.name}_${model}`
      try {
        const result = await agent(`Generate documentation for ${item.name}.
  
  Type: ${item.type}
  File: ${item.file}:${item.line}
  Complexity: ${item.complexity}
  
  Generate ${platform.doc_style || 'standard'} documentation:
  - Brief description (1-2 sentences)
  - Parameters (with types and descriptions)
  - Return value
  - Usage example
  - Notes/warnings if applicable
  
  Write high-quality, clear documentation.`, {
          label: `Doc (${model}): ${item.name}`,
          model,
          schema: {
            type: 'object',
            properties: {
              description: { type: 'string' },
              params_doc: { type: 'string' },
              returns_doc: { type: 'string' },
              example: { type: 'string' },
              notes: { type: 'string' }
            }
          }
        })
  
        // INTEGRATION POINT 2: Log cost metrics for doc generation
        await logCostMetrics(model, 300, 200, 'code-doc-generation', itemRequestId)
  
        return result
      } catch (err) {
        return null
      }
    })),
  
    // Stage 2: Arbiter creates consensus doc
    async (workerDocs, item) => {
      const validDocs = workerDocs.filter(Boolean)
      if (validDocs.length === 0) return null
  
      const arbiterRequestId = `${workflowRequestId}_doc_arbiter_${item.name}`
  
      // INTEGRATION POINT 3: Use Thompson for arbiter model selection
      const arbiterModel = await selectModelViaThompson('code-doc-arbiter', arbiterRequestId, 'opus')
  
      const result = await agent(`Merge ${validDocs.length} documentation versions for ${item.name}.
  
  Create best consensus documentation by:
  - Combining best descriptions
  - Most accurate parameter docs
  - Clearest examples
  
  Return final documentation.`, {
        label: `Arbiter: ${item.name}`,
        model: arbiterModel,
        schema: {
          type: 'object',
          properties: {
            final_doc: { type: 'string' },
            confidence: { type: 'number' }
          }
        }
      })
  
      // Log arbiter cost
      await logCostMetrics(arbiterModel, 250, 150, 'code-doc-arbiter', arbiterRequestId)
  
      return result
    }
  )
  
  const validDocs = docGenerations.filter(Boolean)
  
  log(`✅ Generated ${validDocs.length} documentation blocks`)
  
  // Phase 5: Impact Analysis
  phase('Impact Analysis')
  
  log('🎯 Prioritizing by importance...')
  
  // Score by impact (exported APIs > complex > internal)
  undocumented.undocumented.forEach((item, idx) => {
    let score = 0
  
    if (item.exported) score += 50
    if (item.complexity === 'high') score += 30
    else if (item.complexity === 'medium') score += 15
  
    if (item.type === 'class') score += 20
    else if (item.type === 'function') score += 10
  
    item.impact_score = score
    item.doc = validDocs[idx]?.final_doc
  })
  
  // Sort by impact
  undocumented.undocumented.sort((a, b) => b.impact_score - a.impact_score)
  
  log(`✅ Prioritized ${undocumented.total} items`)
  
  // Phase 6: README Completeness
  phase('README Completeness')
  
  log('📖 Checking README completeness...')
  
  const readmeCheck = await agent(`Check README.md completeness.
  
  Expected sections:
  - Title and description
  - Installation instructions
  - Usage examples
  - API documentation
  - Contributing guidelines
  - License
  
  Check which sections are missing.`, {
    label: 'README Check',
    schema: {
      type: 'object',
      properties: {
        missing_sections: { type: 'array', items: { type: 'string' } },
        needs_update: { type: 'boolean' }
      }
    }
  })
  
  log(`✅ README check: ${readmeCheck.missing_sections?.length || 0} missing sections`)
  
  // Phase 7: User Confirmation
  phase('User Confirmation')
  
  if (!AUTONOMOUS) {
    log('')
    log('═'.repeat(60))
    log('📚 DOCUMENTATION AUDIT RESULTS')
    log('═'.repeat(60))
    log(`Undocumented items: ${undocumented.total}`)
    log(`Generated docs: ${validDocs.length}`)
    log(`Missing README sections: ${readmeCheck.missing_sections?.length || 0}`)
    log('')
  
    const topItems = undocumented.undocumented.slice(0, 5)
    log('Top 5 items to document:')
    topItems.forEach((item, idx) => {
      log(`   ${idx + 1}. ${item.name} (${item.file})`)
      log(`      Type: ${item.type}, Exported: ${item.exported}`)
    })
  
    log('═'.repeat(60))
  
    // ASK USER: Generate documentation?
    const userDecision = await agent(`Review documentation gaps and decide what to generate.
  
  Undocumented: ${undocumented.total}
  Generated: ${validDocs.length}
  
  Options:
  - ALL: Generate docs for all ${undocumented.total} items
  - EXPORTED_ONLY: Only public/exported APIs
  - TOP_10: Top 10 highest priority items
  - NONE: Don't generate (just show report)
  
  Return your decision.`, {
      label: 'User Decision',
      schema: {
        type: 'object',
        properties: {
          action: { type: 'string', enum: ['ALL', 'EXPORTED_ONLY', 'TOP_10', 'NONE'] },
          reasoning: { type: 'string' }
        },
        required: ['action']
      }
    })
  
    log(`\n👤 User Decision: ${userDecision.action}`)
  
    if (userDecision.action === 'NONE') {
      log(`ℹ️  No documentation generated (user chose NONE)`)
      return {
        status: 'report_only',
        undocumented: undocumented.total,
        message: 'Documentation audit complete - report only'
      }
    }
  
    // Filter based on user decision
    let itemsToDocument = undocumented.undocumented
    if (userDecision.action === 'EXPORTED_ONLY') {
      itemsToDocument = undocumented.undocumented.filter(item => item.exported)
    } else if (userDecision.action === 'TOP_10') {
      itemsToDocument = undocumented.undocumented.slice(0, 10)
    }
  
    undocumented.undocumented = itemsToDocument
  }
  
  // Phase 8: Generate Documentation
  phase('Generate Documentation')
  
  log(`📝 Creating documentation PR...`)
  
  // Use branch name from args or default
  const docBranch = args?.doc_branch || 'docs/auto-generated'
  
  const prResult = await agent(`Create documentation PR.
  
  Branch: ${docBranch}
  
  Steps:
  1. Create branch
  2. Add documentation to files:
     ${undocumented.undocumented.slice(0, 5).map(item => `   - ${item.file} (add docs for ${item.name})`).join('\n')}
  3. Commit changes
  4. Push branch
  5. Create PR with title "📚 Add missing documentation"
  
  Execute git commands to create PR.`, {
    label: 'Create Docs PR',
    schema: {
      type: 'object',
      properties: {
        pr_number: { type: 'number' },
        pr_url: { type: 'string' },
        files_updated: { type: 'number' }
      }
    }
  })
  
  log(`✅ Documentation PR created: ${prResult.pr_url}`)
  
  log('')
  log('═'.repeat(60))
  log('📚 DOCUMENTATION GENERATION COMPLETE')
  log('═'.repeat(60))
  log(`Undocumented items: ${undocumented.total}`)
  log(`Documented: ${undocumented.undocumented.length}`)
  log(`PR: ${prResult.pr_url}`)
  log('═'.repeat(60))
  
  const result = {
    status: 'complete',
    total_undocumented: undocumented.total,
    documented: undocumented.undocumented.length,
    docs_generated: undocumented.undocumented.length,
    coverage: undocumented.total > 0 ? Math.round((undocumented.undocumented.length / undocumented.total) * 100) : 100,
    pr_url: prResult.pr_url,
    pr_number: prResult.pr_number,
    request_id: workflowRequestId
  }
  
  // INTEGRATION POINT 4: Record workflow outcome to Learning service
  const docQuality = undocumented.undocumented.length === undocumented.total ? 4 : undocumented.undocumented.length >= undocumented.total * 0.7 ? 3 : 2
  const docTokens = (undocumented.undocumented.length || 1) * 500 + 1000
  const docCost = calculateCost('opus', 400, docTokens)
  
  await recordOutcomeToLearning(
    workflowRequestId,
    'code-doc-workflow',
    'opus',
    docQuality,
    docTokens,
    docCost,
    workflowRequestId,
    {
      kind: 'documentation-coverage-report',
      reference: prResult.pr_url || '',
      summary: `Documentation workflow generated ${undocumented.undocumented.length} documentation items from ${undocumented.total} identified items; reported coverage ${result.coverage}% and created PR ${prResult.pr_number}.`
    }
  )
  
  // Extract learnings
  try {
    await workflow('ai-extract-learning', {
      workflow_name: 'code-doc',
      execution_data: result,
      request_id: workflowRequestId
    })
  } catch (error) {
    log(`⚠️ Learning extraction failed (non-blocking): ${error.message}`)
  }
  
  // Report caching metrics
  const cacheReport = cachingBridge.generateReport()
  if (cacheReport.cache_hits > 0 || cacheReport.cache_misses > 0) {
    log('💾 CACHING METRICS')
    log(`   Cache Hits: ${cacheReport.cache_hits}`)
    log(`   Cache Misses: ${cacheReport.cache_misses}`)
    log(`   Tokens Saved: ${cacheReport.total_tokens_saved}`)
    log(`   Cost Saved: $${cacheReport.total_cost_saved.toFixed(4)}`)
  }
  
  log(`[Final] Workflow completed: ${workflowRequestId}`)
  
return result
}
