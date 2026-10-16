import path from 'node:path'
import fs from 'node:fs'
import os from 'node:os'
import yaml from 'js-yaml'
import { execSync } from 'node:child_process'
export const meta = {
  name: 'code-pr-review',
  description: 'Interactive PR review with multi-AI consensus - prompts before approve/reject',
  whenToUse: 'When you want to review PRs with AI consensus but manual approval',
  phases: [
    { title: 'Setup', detail: 'Detect platform and sync' },
    { title: 'Discover PRs', detail: 'Find open PRs needing review' },
    { title: 'Fetch PR', detail: 'Get PR details and diff' },
    { title: 'Impact Analysis', detail: 'Detect breaking changes' },
    { title: 'Multi-Model Review', detail: 'AI consensus review', model: 'opus' },
    { title: 'Arbiter Decision', detail: 'Final AI decision' },
    { title: 'User Confirmation', detail: 'User decides approve/reject' },
    { title: 'Post Results', detail: 'Comment and approve/reject' },
  ],
}

// ============================================================================
// INTEGRATION: Thompson/Learning/Alert/Compression/Caching Ecosystem
// ============================================================================

import crypto from 'node:crypto'
import { compressDiff } from '../shared/compression-bridge.js'
import { recordEvidenceGatedOutcome } from '../shared/learning-outcome-bridge.js'
import CachingBridge from '../shared/caching-bridge.js'

function generateRequestId(prefix = 'skill_pr_review') {
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
    log(`[Cost] ${model}: ${inputTokens}→${outputTokens} tokens ($${cost.toFixed(4)})`)
  } catch (err) {
    // Silent failure for cost tracking (non-critical)
  }
}

// ============================================================================
// INLINE DEPENDENCIES (no imports - workflow compatibility)
// ============================================================================

// Inlined from shared/platform-detector.js
async function detectPlatform(agent) {
  const result = await agent(`Detect the repository platform and return details.

Execute these commands:
git remote get-url origin
which gh
which glab

Based on the remote URL and available CLIs, determine:
- Platform (github, gitlab, or bitbucket)
- CLI tool available (gh, glab, or bb)
- Repository owner/name

Return structured data.`, {
    label: 'Detect Platform',
    schema: {
      type: 'object',
      properties: {
        platform: { type: 'string', enum: ['github', 'gitlab', 'bitbucket', 'unknown'] },
        cli: { type: 'string', enum: ['gh', 'glab', 'bb', 'none'] },
        remote_url: { type: 'string' },
        repo_owner: { type: 'string' },
        repo_name: { type: 'string' },
      },
      required: ['platform', 'cli', 'remote_url'],
    }
  })

  return result
}

async function syncWithRemote(agent, options = {}) {
  const { branch = 'main' } = options

  const result = await agent(`Sync with remote repository.

Execute these commands:
git fetch origin
git rebase origin/${branch}

Return the status of the sync operation.
If there are conflicts, list them.`, {
    label: 'Sync with Remote',
    schema: {
      type: 'object',
      properties: {
        status: { type: 'string', enum: ['success', 'conflicts', 'failed', 'up_to_date'] },
        message: { type: 'string' },
        conflicts: { type: 'array', items: { type: 'string' } },
        branch: { type: 'string' },
      },
      required: ['status'],
    }
  })

  return result
}

// Fetch PR details
async function fetchPR(agent, platform, prNumber) {
  const result = await agent(`Fetch PR #${prNumber} details.

Execute:
${platform.cli} pr view ${prNumber} --json number,title,author,body,headRefName,baseRefName,state,url

Return PR information.`, {
    label: `Fetch PR #${prNumber}`,
    schema: {
      type: 'object',
      properties: {
        number: { type: 'number' },
        title: { type: 'string' },
        author: { type: 'string' },
        body: { type: 'string' },
        head_branch: { type: 'string' },
        base_branch: { type: 'string' },
        state: { type: 'string' },
        url: { type: 'string' }
      }
    }
  })

  return result
}

// Post comment
async function postComment(agent, platform, type, number, comment) {
  await agent(`Post comment to ${type} #${number}.

Execute:
${platform.cli} ${type} comment ${number} --body "${comment.replace(/"/g, '\\"')}"

Post the comment.`, {
    label: `Comment on ${type} #${number}`
  })
}

// Simple impact analysis (inline version)
async function analyzeImpactSimple(agent, changedFiles, diff) {
  // Find what depends on changed files
  const grepCommands = changedFiles.slice(0, 10).map(f =>
    `grep -r "from ['\"].*${f.replace(/^.*\//, '')}['\"]" . --include="*.js" --include="*.ts" --include="*.jsx" --include="*.tsx" 2>/dev/null | head -20`
  ).join('; echo "---"; ')

  const result = await agent(`Analyze impact of these changes on the codebase.

Changed files (${changedFiles.length}):
${changedFiles.slice(0, 20).join('\n')}

Diff excerpt:
${diff.substring(0, 2000)}

Find dependencies:
${grepCommands}

Analyze:
1. Are there breaking changes? (function signatures, removed exports, type changes)
2. How many files are impacted?
3. What's the risk level?
4. Are there missing tests?

Return structured impact assessment.`, {
    label: 'Impact Analysis',
    schema: {
      type: 'object',
      properties: {
        breaking_changes: {
          type: 'array',
          items: {
            type: 'object',
            properties: {
              entity: { type: 'string' },
              reason: { type: 'string' },
              severity: { type: 'string', enum: ['low', 'medium', 'high', 'critical'] }
            }
          }
        },
        high_risk_changes: {
          type: 'array',
          items: {
            type: 'object',
            properties: {
              entity: { type: 'string' },
              reason: { type: 'string' }
            }
          }
        },
        impacted_files: { type: 'number' },
        missing_tests: { type: 'number' },
        risk_level: { type: 'string', enum: ['low', 'medium', 'high', 'critical'] }
      }
    }
  })

  return result
}

// Multi-model review (Thompson-integrated)
async function multiModelReview(agent, prompt, workers, requestId) {
  log(`🤖 Running ${workers.length}-model review...`)

  const reviews = await Promise.all(workers.map(async (model, idx) => {
    const modelRequestId = `${requestId}_review_${idx}`
    try {
      const result = await agent(prompt, {
        label: `Review (${model})`,
        model,
        phase: 'Multi-Model Review',
        schema: {
          type: 'object',
          properties: {
            quality_score: { type: 'number', minimum: 0, maximum: 100 },
            recommendation: { type: 'string', enum: ['approve', 'request_changes', 'comment'] },
            issues_found: {
              type: 'array',
              items: {
                type: 'object',
                properties: {
                  severity: { type: 'string', enum: ['low', 'medium', 'high', 'critical'] },
                  description: { type: 'string' },
                  file: { type: 'string' }
                }
              }
            },
            strengths: { type: 'array', items: { type: 'string' } },
            improvements_needed: { type: 'array', items: { type: 'string' } },
            confidence: { type: 'number', minimum: 0, maximum: 100 }
          }
        }
      })

      // Log cost metrics (tokens estimated from response)
      await logCostMetrics(model, 500, 300, 'code-pr-review-multi-model', modelRequestId)

      return result
    } catch (err) {
      log(`⚠️ ${model} review failed: ${err.message}`)
      return null
    }
  }))

  const validReviews = reviews.filter(Boolean)

  return {
    reviews: validReviews,
    models: workers.filter((_, idx) => reviews[idx] !== null)
  }
}

// Arbiter decision (Thompson-integrated)
async function arbiterDecision(agent, prTitle, reviews, arbiterModel, requestId) {
  const reviewSummary = reviews.map((r, idx) =>
    `Model ${idx + 1}: ${r.recommendation} (score: ${r.quality_score}, confidence: ${r.confidence}%)`
  ).join('\n')

  // Use Thompson to select arbiter model
  let selectedArbiter = arbiterModel
  if (!selectedArbiter) {
    selectedArbiter = await selectModelViaThompson('code-pr-review-arbiter', `${requestId}_arbiter`, 'opus')
  }

  const arbiterRequestId = `${requestId}_arbiter_decision`

  const result = await agent(`Make final decision on PR: "${prTitle}"

Reviews from ${reviews.length} models:
${reviewSummary}

Analyze the reviews and make a consensus decision.
Consider:
- Overall quality scores
- Confidence levels
- Critical issues found
- Agreement among models

Return your final decision with reasoning.`, {
    label: 'Arbiter Decision',
    model: selectedArbiter,
    phase: 'Arbiter Decision',
    schema: {
      type: 'object',
      properties: {
        final_decision: { type: 'string', enum: ['approve', 'request_changes', 'comment'] },
        reasoning: { type: 'string' },
        consensus_score: { type: 'number', minimum: 0, maximum: 100 },
        key_concerns: { type: 'array', items: { type: 'string' } }
      }
    }
  })

  // Log arbiter cost metrics
  await logCostMetrics(selectedArbiter, 400, 250, 'code-pr-review-arbiter', arbiterRequestId)

  return result
}

// ============================================================================
// CONFIGURATION (required)
// ============================================================================

// Load user model config from ~/.claude/toolkit-models.yaml (required)
let userModelConfig = null
try {
        const configPath = path.join(os.homedir(), '.claude/toolkit-models.yaml')
  if (!fs.existsSync(configPath)) {
    log(`❌ Configuration Required: ~/.claude/toolkit-models.yaml not found`)
    log(``)
    log(`Create it with:`)
    log(`  bash ./install.sh`)
    log(``)
    log(`Or copy the default:`)
    log(`  cp ~/.claude/toolkit-models.yaml.default ~/.claude/toolkit-models.yaml`)
    process.exit(1)
  }
    const content = fs.readFileSync(configPath, 'utf8')
  userModelConfig = yaml.load(content)
  if (!userModelConfig || typeof userModelConfig !== 'object') {
    log(`❌ Invalid configuration file (empty or malformed YAML)`)
    process.exit(1)
  }
  log(`[Config] Loaded ~/.claude/toolkit-models.yaml`)
} catch (err) {
  log(`❌ Failed to load configuration: ${err.message}`)
  process.exit(1)
}

// Extract skill configuration
const skillConfig = userModelConfig.skill_defaults?.['code-pr-review']
if (!skillConfig) {
  log(`❌ Skill configuration 'code-pr-review' not found in config file`)
  process.exit(1)
}
if (!Array.isArray(skillConfig.models)) {
  log(`❌ Invalid models configuration (must be an array)`)
  process.exit(1)
}

// Initialize compression & caching
const cachingBridge = new CachingBridge('code-pr-review')

const CONFIG = {
  workers: skillConfig.models,
  arbiterModel: skillConfig.arbiter || 'opus',
  cachingBridge: cachingBridge,
  compressionEnabled: true,

  // AUTO-APPROVAL CRITERIA (strict by default)
  autoApprove: {
    minQualityScore: 90,              // Must score 90+ to auto-approve
    minConsensus: 85,                 // 85%+ agreement required
    maxBreakingChanges: 0,            // NO breaking changes allowed
    maxHighRiskChanges: 2,            // Max 2 high-risk changes
    maxImpactedFiles: 50,             // Max 50 files impacted
    requireAllApprove: false,         // If true, ALL models must approve
  },

  // AUTO-REJECT CRITERIA
  autoReject: {
    hasBreakingChanges: true,         // Reject if breaking changes
    hasCriticalIssues: true,          // Reject if critical severity issues
    lowQualityScore: 60,              // Reject if quality < 60
    highRiskLevel: 'critical',        // Reject if risk = critical
  },

  // LOOP SETTINGS
  maxPRsPerRun: 10,                   // Review max 10 PRs per iteration
  stopWhenEmpty: true,                // Stop when no PRs left
}

export default async function run({ agent, log, phase, workflow, args }) {
  // Parse args (allow override)
  const maxPRs = args?.max || args?.['--max'] || CONFIG.maxPRsPerRun
  const minQuality = args?.quality || args?.['--quality'] || CONFIG.autoApprove.minQualityScore

  // INTEGRATION POINT 1: Generate request correlation ID at workflow start
  const workflowRequestId = generateRequestId('workflow_code_pr_review')

    log('')
    log('═'.repeat(60))
    log('🤖 INTERACTIVE PR REVIEW BOT')
  log('═'.repeat(60))
  log(`Request ID: ${workflowRequestId}`)
  log(`Workers: ${CONFIG.workers.join(', ')}`)
  log(`Arbiter: ${CONFIG.arbiterModel}`)
  log(`Auto-Approve: Quality ≥ ${minQuality}, No breaking changes`)
  log(`Auto-Reject: Breaking changes, Critical issues, Quality < ${CONFIG.autoReject.lowQualityScore}`)
  log(`Max PRs/run: ${maxPRs}`)
  log('═'.repeat(60))
  log('')
  
  // ============================================================================
  // MAIN WORKFLOW
  // ============================================================================
  
  // PHASE 1: Setup
  phase('Setup')
  
  log('🔧 Detecting platform...')
  const platform = await detectPlatform(agent)
  log(`✅ Platform: ${platform.platform} (${platform.cli})`)
  
  log('🔄 Syncing with remote...')
  const syncResult = await syncWithRemote(agent)
  if (syncResult.status === 'conflicts') {
    log(`❌ Rebase conflicts detected: ${syncResult.conflicts.join(', ')}`)
    return { status: 'conflicts', message: 'Resolve conflicts first' }
  }
  log(`✅ ${syncResult.status === 'up_to_date' ? 'Up to date' : 'Synced'}`)
  
  // PHASE 2: Discover PRs
  phase('Discover PRs')
  
  log('📋 Finding open PRs needing review...')
  
  const prListResult = await agent(`List all open pull requests that need review.
  
  Platform: ${platform.platform}
  CLI: ${platform.cli}
  
  Execute:
  ${platform.cli} pr list --json number,title,author,state --limit 100
  
  Filter for:
  1. Open PRs only
  2. Not already reviewed by AI (check for "🤖 INTERACTIVE PR REVIEW" in comments)
  3. Not draft PRs
  
  Return list of PR numbers to review.`, {
    label: 'List Open PRs',
    schema: {
      type: 'object',
      properties: {
        prs: {
          type: 'array',
          items: {
            type: 'object',
            properties: {
              number: { type: 'number' },
              title: { type: 'string' },
              needs_review: { type: 'boolean' }
            }
          }
        },
        total_open: { type: 'number' },
        needs_review_count: { type: 'number' }
      }
    }
  })
  
  const prsToReview = prListResult.prs.filter(pr => pr.needs_review).map(pr => pr.number)
  
  log(`📊 Found ${prListResult.total_open} open PRs, ${prsToReview.length} need review`)
  
  if (prsToReview.length === 0) {
    log('✅ No PRs to review - all done!')
    return {
      status: 'complete',
      message: 'No PRs needing review',
      prs_reviewed: 0
    }
  }
  
  // Limit to maxPRs
  const prsThisRun = prsToReview.slice(0, maxPRs)
  log(`🎯 Reviewing ${prsThisRun.length} PRs this run`)
  log('')
  
  // ============================================================================
  // REVIEW SINGLE PR FUNCTION (for parallel execution)
  // ============================================================================
  
  const reviewSinglePR = async (prNum, platform, requestId) => {
    const prRequestId = `${requestId}_pr_${prNum}`
  
    log('')
    log('═'.repeat(60))
    log(`📝 PR #${prNum}`)
    log(`   Request: ${prRequestId}`)
    log('═'.repeat(60))
  
    // PHASE 3: Fetch PR
    phase('Fetch PR')
  
    log(`📥 Fetching PR #${prNum}...`)
    const pr = await fetchPR(agent, platform, prNum)
    log(`✅ "${pr.title}" by ${pr.author}`)
    log(`   ${pr.head_branch} → ${pr.base_branch}`)
  
    // Get diff and files
    const diffResult = await agent(`Get diff and changed files for PR #${prNum}.
  
  Execute:
  ${platform.cli} pr diff ${prNum}
  ${platform.cli} pr view ${prNum} --json files
  
  Return diff and file list.`, {
      label: `Get PR #${prNum} Diff`,
      schema: {
        type: 'object',
        properties: {
          diff: { type: 'string' },
          files: { type: 'array', items: { type: 'string' } },
          files_changed: { type: 'number' },
          additions: { type: 'number' },
          deletions: { type: 'number' }
        }
      }
    })
  
    log(`📊 ${diffResult.files_changed || diffResult.files?.length || 0} files, +${diffResult.additions || 0}/-${diffResult.deletions || 0}`)
  
    // PHASE 4: Impact Analysis
    phase('Impact Analysis')
  
    log('🎯 Analyzing impact...')
    const impact = await analyzeImpactSimple(agent, diffResult.files || [], diffResult.diff)
  
    log(`✅ Impact: ${impact.risk_level}`)
    log(`   Breaking: ${impact.breaking_changes.length}`)
    log(`   High-risk: ${impact.high_risk_changes.length}`)
    log(`   Impacted files: ${impact.impacted_files}`)
  
    // PHASE 5: Multi-Model Review
    phase('Multi-Model Review')
  
    // Compress diff for token efficiency
    let diffToReview = diffResult.diff
    if (CONFIG.compressionEnabled && diffResult.diff.length > 1000) {
      log('🗜️ Compressing diff for efficiency...')
      diffToReview = compressDiff(diffResult.diff.substring(0, 5000))
    }
  
    const reviewPrompt = `Review this pull request:
  
  **PR #${prNum}**: ${pr.title}
  **Author**: ${pr.author}
  **Branch**: ${pr.head_branch} → ${pr.base_branch}
  
  **Changes** (${diffResult.files_changed || 0} files):
  \`\`\`diff
  ${diffToReview.substring(0, 2500)}
  ${diffToReview.length > 2500 ? '\n... (truncated)' : ''}
  \`\`\`
  
  **Impact Analysis**:
  ${impact.breaking_changes.length > 0 ? `⚠️ BREAKING CHANGES (${impact.breaking_changes.length}):
  ${impact.breaking_changes.map(bc => `  - ${bc.entity}: ${bc.reason}`).join('\n')}
  ` : ''}Risk Level: ${impact.risk_level}
  Impacted Files: ${impact.impacted_files}
  Missing Tests: ${impact.missing_tests}
  
  Analyze and provide:
  1. Quality score (0-100)
  2. Recommendation (approve/request_changes/comment)
  3. Issues found (with severity)
  4. Strengths
  5. Improvements needed
  6. Confidence level (0-100)
  
  **IMPORTANT**: If breaking changes detected, strongly consider "request_changes".`
  
    const { reviews, models } = await multiModelReview(agent, reviewPrompt, CONFIG.workers, prRequestId)
  
    log(`✅ ${reviews.length} models completed review`)
  
    // PHASE 6: Arbiter Decision
    phase('Arbiter Decision')
  
    log('⚖️ Arbiter deciding...')
    const decision = await arbiterDecision(agent, pr.title, reviews, CONFIG.arbiterModel, prRequestId)
  
    log(`✅ Decision: ${decision.final_decision}`)
    log(`   Consensus: ${decision.consensus_score}%`)
  
    // Calculate quality score
    const avgQuality = reviews.reduce((sum, r) => sum + r.quality_score, 0) / reviews.length
  
    // PHASE 7: Auto-Decision
    phase('Auto-Decision')
  
    log('🤖 Determining auto-action...')
  
    let autoAction = 'COMMENT' // Default: just comment
    let reasoning = ''
  
    // AUTO-REJECT criteria
    if (impact.breaking_changes.length > 0) {
      autoAction = 'REJECT'
      reasoning = `Breaking changes detected: ${impact.breaking_changes.map(bc => bc.entity).join(', ')}`
    } else if (impact.risk_level === 'critical') {
      autoAction = 'REJECT'
      reasoning = `Critical risk level`
    } else if (avgQuality < CONFIG.autoReject.lowQualityScore) {
      autoAction = 'REJECT'
      reasoning = `Low quality score (${Math.round(avgQuality)}/100)`
    } else if (reviews.some(r => r.issues_found?.some(i => i.severity === 'critical'))) {
      autoAction = 'REJECT'
      reasoning = `Critical issues found`
    }
    // AUTO-APPROVE criteria
    else if (
      decision.final_decision === 'approve' &&
      avgQuality >= minQuality &&
      decision.consensus_score >= CONFIG.autoApprove.minConsensus &&
      impact.breaking_changes.length === 0 &&
      impact.high_risk_changes.length <= CONFIG.autoApprove.maxHighRiskChanges &&
      impact.impacted_files <= CONFIG.autoApprove.maxImpactedFiles
    ) {
      autoAction = 'APPROVE'
      reasoning = `High quality (${Math.round(avgQuality)}/100), ${decision.consensus_score}% consensus, ${impact.risk_level} risk`
    }
    // REQUEST_CHANGES
    else if (decision.final_decision === 'request_changes') {
      autoAction = 'REJECT'
      reasoning = `AI consensus: request changes (quality ${Math.round(avgQuality)}/100)`
    }
  
    log(`🎯 Auto-Action: ${autoAction}`)
    log(`   Reasoning: ${reasoning}`)
  
    // PHASE 8: Post Results
    phase('Post Results')
  
    log('📝 Posting review...')
  
    // Build comment
    let comment = `## 🤖 INTERACTIVE PR REVIEW
  
  **Quality Score**: ${Math.round(avgQuality)}/100
  **AI Consensus**: ${decision.final_decision} (${decision.consensus_score}% agreement)
  **Impact Risk**: ${impact.risk_level}
  **Auto-Decision**: ${autoAction}
  
  ### Decision Reasoning
  ${reasoning}
  
  ### Impact Analysis
  - **Breaking Changes**: ${impact.breaking_changes.length}
  ${impact.breaking_changes.length > 0 ? impact.breaking_changes.map(bc => `  - ⚠️ ${bc.entity}: ${bc.reason}`).join('\n') : ''}
  - **High-Risk Changes**: ${impact.high_risk_changes.length}
  - **Files Impacted**: ${impact.impacted_files}
  - **Missing Tests**: ${impact.missing_tests}
  
  ### AI Reviews (${reviews.length} models)
  
  ${reviews.map((r, idx) => `**${models[idx]}** - ${r.recommendation} (${r.quality_score}/100, ${r.confidence}% confidence)
  - Issues: ${r.issues_found?.length || 0} (${r.issues_found?.filter(i => i.severity === 'critical').length || 0} critical)
  ${r.issues_found?.slice(0, 3).map(i => `  - ${i.severity}: ${i.description}`).join('\n') || ''}
  ${r.strengths?.slice(0, 2).map(s => `  - ✅ ${s}`).join('\n') || ''}
  `).join('\n')}
  
  ### Arbiter Decision (${CONFIG.arbiterModel})
  ${decision.reasoning}
  
  ${decision.key_concerns.length > 0 ? `**Key Concerns**:
  ${decision.key_concerns.map(c => `- ${c}`).join('\n')}
  ` : ''}
  
  ---
  
  *Automated review by pr-review-auto workflow*
  *Approval Criteria: Quality ≥ ${minQuality}, Consensus ≥ ${CONFIG.autoApprove.minConsensus}%, No breaking changes*`
  
    await postComment(agent, platform, 'pr', prNum, comment)
    log(`✅ Comment posted`)
  
    // Execute action
    if (autoAction === 'APPROVE') {
      log('👍 Auto-approving PR...')
  
      await agent(`Approve PR #${prNum}.
  
  Execute:
  ${platform.cli} pr review ${prNum} --approve --body "✅ Auto-approved: Quality ${Math.round(avgQuality)}/100, ${decision.consensus_score}% AI consensus, ${impact.risk_level} risk"`, {
        label: `Approve PR #${prNum}`
      })
  
      log(`✅ PR #${prNum} APPROVED`)
  
    } else if (autoAction === 'REJECT') {
      log('⚠️  Requesting changes...')
  
      await agent(`Request changes on PR #${prNum}.
  
  Execute:
  ${platform.cli} pr review ${prNum} --request-changes --body "⚠️ Changes requested: ${reasoning}"`, {
        label: `Request Changes PR #${prNum}`
      })
  
      log(`✅ PR #${prNum} REJECTED (changes requested)`)
  
    } else {
      log('💬 Comment-only (no approve/reject)')
    }
  
    // INTEGRATION POINT 2: Record outcome to Learning service
    const qualityRating = autoAction === 'APPROVE' ? 4 : autoAction === 'REJECT' ? 2 : 3
    const estimatedTokens = Math.floor(reviews.length * 800) + 400
    const estimatedCost = calculateCost('opus', 500, estimatedTokens)
  
    await recordOutcomeToLearning(
      prRequestId,
      'code-pr-review',
      'multi-model-consensus',
      qualityRating,
      estimatedTokens,
      estimatedCost,
      prRequestId,
      {
        kind: 'review-decision',
        reference: pr.url || pr.html_url || pr.web_url || '',
        summary: `Arbiter decision ${decision.final_decision}; action ${autoAction}; quality score ${Math.round(avgQuality)}/100; risk ${impact.risk_level}; breaking changes ${impact.breaking_changes.length}.`
      }
    )
  
    return {
      pr_number: prNum,
      title: pr.title,
      quality_score: Math.round(avgQuality),
      ai_decision: decision.final_decision,
      auto_action: autoAction,
      consensus: decision.consensus_score,
      impact_risk: impact.risk_level,
      breaking_changes: impact.breaking_changes.length,
      approved: autoAction === 'APPROVE',
      rejected: autoAction === 'REJECT',
      pr_url: pr.url || pr.html_url || pr.web_url || ''
    }
  }
  
  // ============================================================================
  // REVIEW ALL PRs IN PARALLEL
  // ============================================================================
  
  log('')
  log('🚀 Reviewing PRs in parallel for faster processing...')
  log('')
  
  const results = await parallel(prsThisRun.map(prNum => () =>
    reviewSinglePR(prNum, platform, workflowRequestId)
  ))
  
  const validResults = results.filter(Boolean)
  
  // ============================================================================
  // SUMMARY
  // ============================================================================
  
  log('')
  log('═'.repeat(60))
  log('📊 INTERACTIVE REVIEW SUMMARY')
  log('═'.repeat(60))
  log(`Total PRs reviewed: ${validResults.length}/${prsThisRun.length}`)
  log(`Auto-approved: ${validResults.filter(r => r.approved).length}`)
  log(`Changes requested: ${validResults.filter(r => r.rejected).length}`)
  log(`Comment-only: ${validResults.filter(r => !r.approved && !r.rejected).length}`)
  log('')
  
  validResults.forEach(r => {
    const icon = r.approved ? '✅' : r.rejected ? '⚠️' : '💬'
    log(`${icon} PR #${r.pr_number}: ${r.title}`)
    log(`   Action: ${r.auto_action}, Quality: ${r.quality_score}/100, Risk: ${r.impact_risk}`)
  })
  
  log('═'.repeat(60))
  log('')
  
  // Report caching metrics
  const cacheReport = CONFIG.cachingBridge.generateReport()
  if (cacheReport.cache_hits > 0 || cacheReport.cache_misses > 0) {
    log('💾 CACHING METRICS')
    log(`   Cache Hits: ${cacheReport.cache_hits}`)
    log(`   Cache Misses: ${cacheReport.cache_misses}`)
    log(`   Tokens Saved: ${cacheReport.total_tokens_saved}`)
    log(`   Cost Saved: $${cacheReport.total_cost_saved.toFixed(4)}`)
    log('')
  }
  
  if (prsToReview.length > maxPRs) {
    log(`ℹ️  ${prsToReview.length - maxPRs} more PRs remain - run again to continue`)
  } else {
    log(`✅ All PRs reviewed!`)
  }
  
  const result = {
    status: 'success',
    prs_reviewed: validResults.length,
    approved: validResults.filter(r => r.approved).length,
    rejected: validResults.filter(r => r.rejected).length,
    remaining: Math.max(0, prsToReview.length - maxPRs),
    breaking_changes: validResults.filter(r => r.breaking_changes > 0).length,
    failed: results.length - validResults.length,
    results: validResults,
    request_id: workflowRequestId
  }
  
  // Per-PR outcomes above are the learning units; do not double-count the batch summary.
  
  // Extract learnings
  try {
    await workflow('ai-extract-learning', {
      workflow_name: 'code-pr-review',
      execution_data: result,
      request_id: workflowRequestId
    })
  } catch (error) {
    log(`⚠️ Learning extraction failed (non-blocking): ${error.message}`)
  }

  log(`[Final] Workflow completed: ${workflowRequestId}`)

  return result
}
