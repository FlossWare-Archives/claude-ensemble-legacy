import { recordEvidenceGatedOutcome } from '../shared/learning-outcome-bridge.js'

export const meta = {
  name: 'code-doc-auto',
  description: 'Autonomous documentation generation - auto-creates documentation PRs',
  whenToUse: 'When you want fully automated documentation generation without manual intervention',
  autonomous: true,
  phases: [
    { title: 'Detect Platform', detail: 'Identify GitHub/GitLab and project type' },
    { title: 'Find Undocumented Code', detail: 'Scan for missing docs' },
    { title: 'Analyze Signatures', detail: 'Extract function/class signatures' },
    { title: 'Multi-AI Doc Generation', detail: 'Generate docs with consensus', model: 'opus' },
    { title: 'Impact Analysis', detail: 'Prioritize by importance' },
    { title: 'README Completeness', detail: 'Check README gaps' },
    { title: 'Auto-Decision', detail: 'Auto-create docs based on criteria' },
    { title: 'Generate Documentation', detail: 'Create PR with docs' },
  ],
}

// ============================================================================
// INTEGRATION: Thompson/Learning/Alert Ecosystem
// ============================================================================

const crypto = require('crypto')

function generateRequestId(prefix = 'skill_code_doc_auto') {
  return `${prefix}_${crypto.randomBytes(6).toString('hex')}`
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

// ============================================================================

const workflowRequestId = generateRequestId('workflow_code_doc_auto')

log('')
log('═'.repeat(60))
log('📝 AUTONOMOUS DOCUMENTATION GENERATOR')
log('═'.repeat(60))
log('This workflow auto-creates documentation PRs')
log(`Request ID: ${workflowRequestId}`)
log('')
log('Auto-decision criteria:')
log('  • All exported/public APIs')
log('  • All high-complexity functions')
log('  • All classes without docs')
log('  • Min confidence ≥80%')
log('═'.repeat(60))
log('')

// Load config (required)
let userModelConfig = null
try {
  const path = require('path')
  const fs = require('fs')
  const configPath = path.expandUser('~/.claude/toolkit-models.yaml')
  if (!fs.existsSync(configPath)) {
    log(`❌ Configuration Required: ~/.claude/toolkit-models.yaml not found`)
    process.exit(1)
  }
  const yaml = require('js-yaml')
  userModelConfig = yaml.load(fs.readFileSync(configPath, 'utf8'))
  log(`[Config] Loaded ~/.claude/toolkit-models.yaml`)
} catch (err) {
  log(`❌ Failed to load config: ${err.message}`)
  process.exit(1)
}

// Auto-decision criteria
const AUTO_GENERATE_CRITERIA = {
  all_exported: true,
  high_complexity: true,
  all_classes: true,
  min_confidence: 0.80
}

log('🔄 Delegating to code-doc workflow with autonomous=true...')
log('')

// Pass through doc_branch from args if provided
const result = await workflow('code-doc', {
  autonomous: true,
  doc_branch: args?.doc_branch,
  request_id: workflowRequestId
})

log('')
log('═'.repeat(60))
log('✅ AUTONOMOUS DOCUMENTATION COMPLETE')
log('═'.repeat(60))
if (result.pr_number) {
  log(`PR created: #${result.pr_number}`)
}
if (result.docs_generated) {
  log(`Docs generated: ${result.docs_generated}`)
}
if (result.coverage) {
  log(`Coverage: ${result.coverage}%`)
}
log('═'.repeat(60))
log('')

// INTEGRATION POINT 1: Record workflow outcome to Learning service
const autoDocQuality = result.coverage >= 80 ? 4 : result.coverage >= 60 ? 3 : 2
const autoDocTokens = (result.docs_generated || 1) * 500 + 1000
const autoDocCost = calculateCost('opus', 400, autoDocTokens)

await recordOutcomeToLearning(
  workflowRequestId,
  'code-doc-auto-workflow',
  'opus',
  autoDocQuality,
  autoDocTokens,
  autoDocCost,
  workflowRequestId,
  {
    kind: 'documentation-coverage-report',
    reference: result.pr_url || result.output_path || '',
    summary: `Autonomous documentation workflow generated ${result.docs_generated || 0} docs; reported coverage ${result.coverage ?? 'unknown'}%; PR ${result.pr_number ?? 'not created'}.`
  }
)

log(`[Final] Workflow completed: ${workflowRequestId}`)

return { ...result, request_id: workflowRequestId }
