import test from 'node:test'
import assert from 'node:assert/strict'
import { deriveStableOutcomeId } from '../shared/learning-outcome-bridge.js'

test('learning outcome identity is stable across retries with identical evidence', () => {
  const evidence = {
    kind: 'review-decision',
    reference: 'https://github.com/example/repo/pull/42',
    summary: 'Arbiter requested changes; high risk.',
  }
  assert.equal(
    deriveStableOutcomeId('code-pr-review', evidence),
    deriveStableOutcomeId('code-pr-review', structuredClone(evidence)),
  )
})

test('learning outcome identity changes when evidence or task type changes', () => {
  const evidence = {
    kind: 'review-decision',
    reference: 'https://github.com/example/repo/pull/42',
    summary: 'Arbiter requested changes; high risk.',
  }
  const base = deriveStableOutcomeId('code-pr-review', evidence)
  assert.notEqual(
    base,
    deriveStableOutcomeId('code-pr-review', { ...evidence, summary: 'Arbiter approved; low risk.' }),
  )
  assert.notEqual(base, deriveStableOutcomeId('code-doc', evidence))
})
