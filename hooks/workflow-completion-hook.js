/**
 * DEPRECATED legacy workflow-completion hook. It is not registered by the
 * supported Claude Code settings and is not the canonical learning path.
 *
 * Supported workflow outcomes flow through:
 * shared/learning-outcome-bridge.js -> tools/record_workflow_outcome.py
 * -> Learning service. That contract stores evidence-backed outcome artifacts
 * and Thompson updates; it does not require semantic embeddings.
 *
 * This legacy module writes directly to an external workflows.* PostgreSQL
 * schema that is not defined or migrated in this repository. Do not re-enable
 * it until that schema, nullability, and all consumers have an explicit,
 * tested contract. In particular, this module does not own embedding generation.
 */

const { getDB } = require('../../learning/postgres-adapter');

/**
 * Record workflow learning to PostgreSQL
 *
 * @param {Object} params - Workflow completion parameters
 * @param {string} params.run_id - Unique workflow run identifier
 * @param {string} params.workflow_name - Name of the workflow
 * @param {Object} params.result - Result object from workflow (may contain reaction_signals)
 * @param {string} params.task_type - Type of task executed
 * @param {string} params.task_summary - Brief task description
 * @param {number} params.quality_score - Quality score (0.0 to 1.0)
 * @param {string} params.outcome - 'success', 'failed', 'error'
 * @param {number} params.duration_ms - Execution duration in milliseconds
 * @param {number} params.cost_usd - Total cost in USD
 * @param {Object} params.metadata - Additional workflow-specific metadata
 * @returns {Promise<Object>} Inserted record
 */
async function recordWorkflowLearning(params) {
  const {
    run_id,
    workflow_name,
    result = {},
    task_type = 'unknown',
    task_summary = '',
    quality_score = null,
    outcome = 'success',
    duration_ms = null,
    cost_usd = null,
    metadata = {}
  } = params;

  if (!run_id) {
    throw new Error('run_id is required for workflow learning recording');
  }

  if (!workflow_name) {
    throw new Error('workflow_name is required for workflow learning recording');
  }

  // Extract reaction signals from result (if present)
  const reactionSignals = result.reaction_signals || result.aggregate || null;
  const taskDifficulty = extractTaskDifficulty(result);
  const modelCount = extractModelCount(result, reactionSignals);
  const polarizationIndex = extractPolarizationIndex(result);
  const behavioralAgreement = extractBehavioralAgreement(result);

  // Determine learning type
  const learningType = reactionSignals ? 'model_behavior' : 'general';

  // Prepare data for insertion
  const learningRecord = {
    run_id,
    workflow_name,
    learning_type: learningType,
    reaction_signals: reactionSignals ? JSON.stringify(reactionSignals) : null,
    task_difficulty: taskDifficulty,
    task_type,
    task_summary: task_summary.substring(0, 500), // Truncate to reasonable length
    quality_score,
    outcome,
    model_count: modelCount,
    polarization_index: polarizationIndex,
    behavioral_agreement: behavioralAgreement,
    duration_ms,
    cost_usd,
    metadata: Object.keys(metadata).length > 0 ? JSON.stringify(metadata) : null,
    embedding: null // Legacy schema field; this deprecated hook does not own embeddings
  };

  // Insert into database
  const db = getDB();

  const sql = `
    INSERT INTO workflows.learnings (
      run_id,
      workflow_name,
      learning_type,
      reaction_signals,
      task_difficulty,
      task_type,
      task_summary,
      quality_score,
      outcome,
      model_count,
      polarization_index,
      behavioral_agreement,
      duration_ms,
      cost_usd,
      metadata,
      embedding
    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16)
    RETURNING id, created_at
  `;

  const values = [
    learningRecord.run_id,
    learningRecord.workflow_name,
    learningRecord.learning_type,
    learningRecord.reaction_signals,
    learningRecord.task_difficulty,
    learningRecord.task_type,
    learningRecord.task_summary,
    learningRecord.quality_score,
    learningRecord.outcome,
    learningRecord.model_count,
    learningRecord.polarization_index,
    learningRecord.behavioral_agreement,
    learningRecord.duration_ms,
    learningRecord.cost_usd,
    learningRecord.metadata,
    learningRecord.embedding
  ];

  const rows = await db.query(sql, values);
  const inserted = rows[0];

  return {
    id: inserted.id,
    created_at: inserted.created_at,
    ...learningRecord
  };
}

/**
 * Record workflow run metadata
 * Call this at workflow start and completion
 *
 * @param {Object} params - Run parameters
 * @param {string} params.run_id - Unique run identifier
 * @param {string} params.workflow_name - Workflow name
 * @param {string} params.status - 'running', 'completed', 'failed', 'error'
 * @param {Object} params.input_args - Input arguments (optional)
 * @param {Object} params.output_result - Output result (optional)
 * @param {string} params.error_message - Error message if failed (optional)
 * @param {number} params.duration_ms - Duration in milliseconds (optional)
 */
async function recordWorkflowRun(params) {
  const {
    run_id,
    workflow_name,
    status,
    input_args = null,
    output_result = null,
    error_message = null,
    duration_ms = null
  } = params;

  if (!run_id || !workflow_name || !status) {
    throw new Error('run_id, workflow_name, and status are required');
  }

  const db = getDB();

  // Check if record exists (update) or create new
  const existing = await db.get(
    'SELECT run_id FROM workflows.runs WHERE run_id = $1',
    [run_id]
  );

  if (existing) {
    // Update existing record
    const sql = `
      UPDATE workflows.runs
      SET status = $1,
          completed_at = CASE WHEN $1 IN ('completed', 'failed', 'error') THEN NOW() ELSE completed_at END,
          output_result = COALESCE($2::jsonb, output_result),
          error_message = COALESCE($3, error_message),
          duration_ms = COALESCE($4, duration_ms)
      WHERE run_id = $5
    `;

    await db.run(sql, [
      status,
      output_result ? JSON.stringify(output_result) : null,
      error_message,
      duration_ms,
      run_id
    ]);
  } else {
    // Insert new record
    const sql = `
      INSERT INTO workflows.runs (
        run_id,
        workflow_name,
        status,
        input_args,
        output_result,
        error_message,
        duration_ms
      ) VALUES ($1, $2, $3, $4, $5, $6, $7)
    `;

    await db.run(sql, [
      run_id,
      workflow_name,
      status,
      input_args ? JSON.stringify(input_args) : null,
      output_result ? JSON.stringify(output_result) : null,
      error_message,
      duration_ms
    ]);
  }

  return { run_id, status };
}

/**
 * Query workflow learnings
 *
 * @param {Object} filters - Query filters
 * @param {string} filters.workflow_name - Filter by workflow name
 * @param {string} filters.task_type - Filter by task type
 * @param {string} filters.task_difficulty - Filter by difficulty ('easy', 'moderate', 'hard')
 * @param {string} filters.outcome - Filter by outcome ('success', 'failed', 'error')
 * @param {number} filters.limit - Limit results (default 100)
 * @returns {Promise<Array>} Learning records
 */
async function queryLearnings(filters = {}) {
  const {
    workflow_name = null,
    task_type = null,
    task_difficulty = null,
    outcome = null,
    limit = 100
  } = filters;

  const db = getDB();

  let sql = 'SELECT * FROM workflows.learnings WHERE 1=1';
  const params = [];
  let paramIdx = 1;

  if (workflow_name) {
    sql += ` AND workflow_name = $${paramIdx}`;
    params.push(workflow_name);
    paramIdx++;
  }

  if (task_type) {
    sql += ` AND task_type = $${paramIdx}`;
    params.push(task_type);
    paramIdx++;
  }

  if (task_difficulty) {
    sql += ` AND task_difficulty = $${paramIdx}`;
    params.push(task_difficulty);
    paramIdx++;
  }

  if (outcome) {
    sql += ` AND outcome = $${paramIdx}`;
    params.push(outcome);
    paramIdx++;
  }

  sql += ` ORDER BY timestamp DESC LIMIT $${paramIdx}`;
  params.push(limit);

  return await db.all(sql, params);
}

/**
 * Get task difficulty statistics
 *
 * @param {string} task_type - Optional task type filter
 * @returns {Promise<Array>} Statistics grouped by task type and difficulty
 */
async function getTaskDifficultyStats(task_type = null) {
  const db = getDB();

  let sql = 'SELECT * FROM workflows.task_difficulty_stats WHERE 1=1';
  const params = [];

  if (task_type) {
    sql += ' AND task_type = $1';
    params.push(task_type);
  }

  sql += ' ORDER BY task_type, task_difficulty';

  return await db.all(sql, params);
}

/**
 * Get workflow performance summary
 *
 * @param {string} workflow_name - Optional workflow name filter
 * @returns {Promise<Array>} Performance statistics
 */
async function getWorkflowPerformance(workflow_name = null) {
  const db = getDB();

  let sql = 'SELECT * FROM workflows.performance_summary WHERE 1=1';
  const params = [];

  if (workflow_name) {
    sql += ' AND workflow_name = $1';
    params.push(workflow_name);
  }

  sql += ' ORDER BY total_runs DESC';

  return await db.all(sql, params);
}

// ============================================================================
// Helper functions for extracting data from workflow results
// ============================================================================

function extractTaskDifficulty(result) {
  // Extract from reaction tracker result
  if (result.task_assessment && result.task_assessment.difficulty) {
    return result.task_assessment.difficulty;
  }

  if (result.aggregate && result.aggregate.estimated_difficulty) {
    return result.aggregate.estimated_difficulty;
  }

  // Fallback to null if not available
  return null;
}

function extractModelCount(result, reactionSignals) {
  if (result.aggregate && result.aggregate.model_count) {
    return result.aggregate.model_count;
  }

  if (reactionSignals && reactionSignals.model_count) {
    return reactionSignals.model_count;
  }

  if (result.workers && Array.isArray(result.workers)) {
    return result.workers.length;
  }

  return null;
}

function extractPolarizationIndex(result) {
  if (result.disagreement && result.disagreement.polarization_index != null) {
    return result.disagreement.polarization_index;
  }

  return null;
}

function extractBehavioralAgreement(result) {
  if (result.disagreement && result.disagreement.behavioral_agreement != null) {
    return result.disagreement.behavioral_agreement;
  }

  return null;
}

// ============================================================================
// Exports
// ============================================================================

module.exports = {
  recordWorkflowLearning,
  recordWorkflowRun,
  queryLearnings,
  getTaskDifficultyStats,
  getWorkflowPerformance
};
