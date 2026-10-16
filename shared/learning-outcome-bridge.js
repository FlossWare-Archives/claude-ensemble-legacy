#!/usr/bin/env node

/**
 * Shared bridge from CE workflow skills to the evidence-gated Learning adapter.
 *
 * Learning owns artifact persistence, outcome deduplication, and Thompson
 * updates. Workflow skills must not bypass that boundary with LearningClient
 * or invent an outcome from successful process exit alone.
 */
import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const MODULE_DIR = path.dirname(fileURLToPath(import.meta.url))
const REPO_ROOT = path.resolve(MODULE_DIR, '..')
const ADAPTER = path.join(REPO_ROOT, 'tools', 'record_workflow_outcome.py')

export function deriveStableOutcomeId(taskType, evidence) {
  const identity = JSON.stringify({ taskType, evidence })
  return `ce-outcome-${createHash('sha256').update(identity).digest('hex')}`
}

export function recordEvidenceGatedOutcome({
  taskType,
  model,
  rating,
  tokens,
  cost,
  evidence,
}) {
  if (!evidence || typeof evidence !== 'object') {
    throw new TypeError('evidence is required; completion alone is not a learning signal')
  }

  // The caller's request IDs are often generated per process. Derive the
  // Learning identity from stable evidence so retrying the same evaluated
  // result cannot apply the same Thompson update twice.
  const stableRunId = deriveStableOutcomeId(taskType, evidence)
  const payload = {
    run_id: stableRunId,
    task_id: stableRunId,
    task_type: taskType,
    model,
    rating,
    tokens,
    cost,
    evidence,
  }
  const stdout = execFileSync(
    process.env.PYTHON || 'python3',
    [ADAPTER],
    {
      cwd: REPO_ROOT,
      env: {
        ...process.env,
        PYTHONPATH: [REPO_ROOT, process.env.PYTHONPATH].filter(Boolean).join(path.delimiter),
      },
      input: JSON.stringify(payload),
      encoding: 'utf8',
      timeout: 8000,
      maxBuffer: 1024 * 1024,
      stdio: ['pipe', 'pipe', 'pipe'],
    },
  )
  const response = JSON.parse(stdout.trim())
  if (response.ok !== true) {
    throw new Error(response.error || 'Learning adapter did not acknowledge the outcome')
  }
  return response
}
