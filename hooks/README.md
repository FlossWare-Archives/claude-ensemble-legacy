# Workflow Hooks

Automatic learning extraction, context loading, and storage for workflow executions.

## Overview

Two primary hooks enable continuous learning across workflow sessions:
1. **Pre-Workflow Context Loader** - Loads similar past workflows before execution
2. **Post-Workflow Learning Extractor** - Extracts learnings after completion

## Architecture

```
┌─────────────────────────────────────────────────┐
│  Pre-Workflow Hook (NEW - ECC #192)             │
│  hooks/pre-workflow-context-loader.js           │
│  - Query similar workflows via pgvector         │
│  - Check model diversity (>70% = echo chamber)  │
│  - Load learnings from similar tasks            │
│  - Inject context into worker prompts           │
└───────────────┬─────────────────────────────────┘
                │
                │ context injected
                ▼
┌─────────────────────────────────────────────────┐
│  Workflow Execution                             │
│  (deep-research, code-solve, etc.)              │
│  - Workers receive past workflow context        │
│  - Diversity recommendations applied            │
└───────────────┬─────────────────────────────────┘
                │
                │ workflow completes
                ▼
┌─────────────────────────────────────────────────┐
│  Post-Workflow Hook                             │
│  ~/.claude/workflows/hooks/                     │
│  post-workflow-learning.js                      │
└───────────────┬─────────────────────────────────┘
                │
                │ extracts learnings
                ▼
┌─────────────────────────────────────────────────┐
│  Learning Extractor                             │
│  Heuristic-based pattern extraction             │
│  OR ai-extract-learning workflow (full AI)      │
└───────────────┬─────────────────────────────────┘
                │
                │ learnings object
                ▼
┌─────────────────────────────────────────────────┐
│  Storage Layer                                  │
│  ~/.claude/learning/storage.js                  │
│  - Generate embeddings                          │
│  - Store in workflows.learnings table           │
│  - Link to execution_summary                    │
└───────────────┬─────────────────────────────────┘
                │
                │ persists to database
                ▼
┌─────────────────────────────────────────────────┐
│  PostgreSQL Database                            │
│  workflows.learnings table                      │
│  - Structured JSONB columns                     │
│  - Vector embeddings for similarity search      │
│  - Foreign key to execution logs                │
└─────────────────────────────────────────────────┘
```

## Components

### 0. Pre-Workflow Context Loader (NEW - ECC #192)
**File:** `hooks/pre-workflow-context-loader.js`

Functions:
- `injectContextIntoWorkflow(taskDescription, options)` - Main entry point, loads similar workflows and prepares context
- `formatContextForPrompt(similarWorkflows)` - Formats workflow data for readable prompt injection
- `diversityCheck(similarWorkflows, db)` - Detects echo chamber effect (>70% same model)

Features:
- **Semantic similarity search** - Uses PostgreSQL+pgvector to find similar past workflows
- **Diversity protection** - Warns if >70% of similar workflows used same model
- **Learning integration** - Loads top learnings from similar tasks
- **Metadata tracking** - Records context usage for feedback loop analysis

Example usage:
```javascript
const { injectContextIntoWorkflow } = require('./hooks/pre-workflow-context-loader.js');

// Before starting workflow
const contextData = await injectContextIntoWorkflow('Research firmware for RAX-75', {
  limit: 5,                  // Load top 5 similar workflows
  check_diversity: true,     // Enable echo chamber detection
  include_learnings: true    // Include learnings table data
});

// Inject into worker prompt
const enhancedPrompt = `${basePrompt}\n\n${contextData.formatted_context}`;

// Track metadata in execution
await db.storeExecution({
  workflow_id: 'wf-123',
  // ...
  metadata: {
    ...contextData.metadata,  // Includes: context_used, context_source, similarity_scores
    // ...
  }
});
```

Returns:
```javascript
{
  previous_similar_workflows: [...],     // Raw workflow objects
  formatted_context: "## Context...",    // Ready for prompt injection
  diversity_analysis: {                  // Echo chamber detection
    has_echo_chamber: false,
    dominant_model: 'opus',
    dominant_percentage: 0.45,
    model_distribution: { opus: 9, sonnet: 6, haiku: 5 },
    recommendation: null
  },
  metadata: {                            // For tracking
    context_used: true,
    context_source: ['wf-abc', 'wf-def'],
    context_count: 5,
    similarity_scores: [0.92, 0.85, 0.78, 0.71, 0.68]
  }
}
```

### 1. Database Schema
**File:** `~/.claude/learning/schema-workflows.sql`

Creates:
- `workflows.learnings` table with JSONB columns
- Vector embedding column (768-dim)
- HNSW index for O(log n) similarity search
- Foreign key to `monitoring.execution_summary`
- Views for easy querying

### 2. Storage Module
**File:** `~/.claude/learning/storage.js`

Functions:
- `storeLearnings(runId, learnings, options)` - Store learning with embedding
- `getLearningsByWorkflow(workflowName)` - Query by workflow
- `findSimilarLearnings(queryText)` - Semantic similarity search
- `getRecentLearnings()` - Recent learnings across all workflows
- `getLearningStats()` - Statistics by workflow

### 3. Embedding Generation
**File:** `~/.claude/learning/embeddings.js`

Features:
- Sentence-transformers integration (all-MiniLM-L6-v2)
- Fallback to hash-based embedding if library unavailable
- Caching for performance
- Cosine similarity computation

### 4. Workflow Integration
**File:** `~/.claude/workflows/ai-extract-learning.js`

Updated to call `storeLearnings()` after extraction:
- Automatically stores learnings to database
- Returns `learning_id` in result
- No longer just logs to console

### 5. Post-Workflow Hook
**File:** `~/.claude/workflows/hooks/post-workflow-learning.js`

Automatically extracts learnings after workflow completion:
- Triggers on `workflow:complete` event
- Skips failed workflows
- Respects `skip_learning_extraction` flag
- Uses heuristic extraction for speed

### 6. Learning Extractor
**File:** `~/.claude/workflows/hooks/learning-extractor.js`

Heuristic-based pattern extraction:
- Faster than AI extraction
- Workflow-specific patterns
- Good for automatic background processing

## Setup

### 1. Create Database Schema
```bash
psql -h /var/run/postgresql -U sfloess -d learning \
  -f ~/.claude/learning/schema-workflows.sql
```

### 2. Install Dependencies (Optional)
```bash
# For high-quality embeddings (recommended)
pip install sentence-transformers

# Test installation
python3 -c "from sentence_transformers import SentenceTransformer; print('OK')"
```

If sentence-transformers is not available, the system will use a fallback hash-based embedding.

### 3. Register Hook (Optional)
Add to `~/.claude/settings.json`:
```json
{
  "hooks": {
    "workflow:complete": [
      "~/.claude/workflows/hooks/post-workflow-learning.js"
    ]
  }
}
```

This enables automatic learning extraction for all workflows.

## Usage

### Manual Learning Extraction

Call the workflow directly:
```javascript
const result = await workflow('ai-extract-learning', {
  run_id: 'my-run-123',
  workflow_name: 'code-solve',
  execution_data: {
    issue_number: 42,
    issue_title: 'Fix null pointer',
    fix_approach: 'Add null check',
    files_changed: ['src/app.js'],
    consensus_score: 0.85
  },
  execution_id: 1234,  // Optional: links to monitoring.execution_summary
  quality_score: 0.9,   // Optional
  strategy: 'base'      // Optional: base/maximum-coverage/quantized
});

console.log(`Stored as learning #${result.learning_id}`);
```

### Query Learnings

```javascript
const { storage } = require('~/.claude/learning/storage');

// Get learnings by workflow
const codeSolveLearnings = await storage.getLearningsByWorkflow('code-solve');

// Semantic search
const similar = await storage.findSimilarLearnings(
  'Java null pointer exceptions',
  10,
  { workflowName: 'code-solve', minQualityScore: 0.7 }
);

// Recent learnings
const recent = await storage.getRecentLearnings(50);

// Statistics
const stats = await storage.getLearningStats();
```

### Direct SQL Queries

```sql
-- Recent learnings with execution details
SELECT * FROM workflows.recent_learnings LIMIT 10;

-- Learning statistics by workflow
SELECT * FROM workflows.learning_stats;

-- Find Java-related learnings
SELECT
  workflow_name,
  recommendations,
  tech_stack
FROM workflows.learnings
WHERE tech_stack @> '["Java"]'
ORDER BY timestamp DESC;

-- Semantic similarity search
SELECT
  *,
  embedding <=> '[0.1, 0.2, ...]'::vector as distance
FROM workflows.learnings
WHERE embedding IS NOT NULL
ORDER BY distance
LIMIT 10;
```

## Learning Object Schema

```javascript
{
  user_patterns: {
    preferences: ['prefers squash merges', 'likes detailed commits'],
    expertise_level: {
      'javascript': 'advanced',
      'python': 'intermediate'
    },
    workflow_usage: ['runs code-review before PRs']
  },
  code_patterns: {
    common_bugs: ['null pointer exceptions', 'missing error handling'],
    architecture_insights: ['uses MVC pattern', 'REST API'],
    tech_stack: ['Node.js', 'PostgreSQL', 'React'],
    quality_trends: ['test coverage improving']
  },
  recommendations: [
    'Add null checks before dereferencing',
    'Consider using TypeScript for type safety'
  ],
  memory_suggestions: [
    {
      type: 'user',
      content: 'User prefers squash merges',
      priority: 'medium'
    }
  ]
}
```

## Integration Points

### Workflow Result Object

To enable automatic learning extraction, workflows should return:
```javascript
return {
  status: 'success',
  // Workflow-specific data
  issue_number: 42,
  fix_approach: 'Added null check',
  files_changed: ['src/app.js'],
  consensus_score: 0.85,
  quality_score: 0.9,

  // Optional: skip automatic extraction
  skip_learning_extraction: false
};
```

### Execution Monitoring

Link learnings to execution logs:
```javascript
// After logging execution
const executionId = await monitor.logExecution({
  model: 'opus',
  workflow: 'code-solve',
  quality_score: 0.9,
  // ...
});

// Extract learnings with link
await workflow('ai-extract-learning', {
  execution_id: executionId,
  // ...
});
```

## Performance

- **Embedding generation:** ~100ms (sentence-transformers) or ~10ms (fallback)
- **Storage:** ~5ms (PostgreSQL insert)
- **Similarity search:** ~0.5ms (HNSW index, O(log n))
- **Total overhead:** ~100-200ms per workflow execution

## Monitoring

Check learning extraction status:
```sql
-- Count learnings by workflow
SELECT
  workflow_name,
  COUNT(*) as total,
  MAX(timestamp) as last_learning
FROM workflows.learnings
GROUP BY workflow_name;

-- Check embedding coverage
SELECT
  COUNT(*) FILTER (WHERE embedding IS NOT NULL) as with_embedding,
  COUNT(*) FILTER (WHERE embedding IS NULL) as without_embedding
FROM workflows.learnings;

-- Recent high-priority memory suggestions
SELECT
  workflow_name,
  jsonb_array_elements(memory_suggestions) ->> 'content' as suggestion
FROM workflows.learnings
WHERE memory_suggestions @> '[{"priority": "high"}]'
ORDER BY timestamp DESC
LIMIT 10;
```

## Troubleshooting

### Embeddings Not Generated
```bash
# Check if sentence-transformers is available
python3 -c "from sentence_transformers import SentenceTransformer; print('OK')"

# If not, install it
pip install sentence-transformers

# Or use fallback (hash-based, lower quality but works)
# System automatically falls back if library unavailable
```

### Hook Not Triggering
```javascript
// Check hook registration
const settings = require('~/.claude/settings.json');
console.log(settings.hooks?.['workflow:complete']);

// Manually trigger for testing
const { onWorkflowComplete } = require('~/.claude/workflows/hooks/post-workflow-learning');
await onWorkflowComplete({
  workflowName: 'code-solve',
  runId: 'test-123',
  result: { issue_number: 42 },
  status: 'success'
});
```

### Foreign Key Violations
```sql
-- Check if execution_id exists
SELECT id FROM monitoring.execution_summary WHERE id = 1234;

-- If not, set execution_id to NULL
UPDATE workflows.learnings
SET execution_id = NULL
WHERE execution_id NOT IN (SELECT id FROM monitoring.execution_summary);
```

## Future Enhancements

1. **Auto-memory generation:** Write high-priority suggestions to `~/.claude/memory/`
2. **Learning clustering:** Group similar learnings for pattern discovery
3. **Recommendation engine:** Suggest workflow improvements based on learnings
4. **Cross-workflow insights:** Find patterns across different workflow types
5. **User expertise tracking:** Build user expertise profile over time
6. **Tech stack detection:** Automatic project tech stack identification


## Lifecycle contract status (Issue #313)

**Important:** the older post-workflow learning example above describes historical behavior, not an approved active hook configuration. `hooks/post-workflow-learning.js` and `hooks/post-task-analysis.js` are disabled because they write learning or Thompson state outside the canonical Learning-service boundary. Do not register them as active hooks.

The supported prompt-time retrieval path is `memory-search-on-prompt.js`, which is read-only and calls Memory REST. Capture adapters must send stable event IDs through the Memory service; Learning-service operations own outcome deduplication and learner delegation; Knowledge promotion is separate and evidence-gated. See [the lifecycle contract](../docs/CLAUDE_CONTEXT_HOOK_LIFECYCLE.md) for responsibilities, retries, and known legacy limitations.

The session-end shell scripts are legacy implementations and must not be registered alongside the canonical `hooks/session-end-memory-capture.js` adapter. The Claude Config installer registers the canonical adapter and unregisters only known legacy script copies matching allowlisted SHA-256 hashes; it preserves the script files and leaves modified or unrecognized registrations untouched.


## RAG memory-search worker count

The `memory-rag-search` hook runs four analysis workers by default (Opus,
Sonnet, Haiku, and Gemini), followed by one arbiter. Set `worker_count` in the
hook's input object to select the first 1-4 workers for that invocation:

```javascript
{ query: "find prior decisions about retries", worker_count: 2 }
```

The default is `4`. Values must be integers from `1` through `4`; missing
values use the default, and invalid values are reported and fall back to `4`.
This setting controls the number of worker analyses, not the arbiter call or
the five parallel content-retrieval operations. Fewer workers generally reduce
model calls and latency but also reduce independent perspectives; four is the
existing behavior, not a claim that it is optimal for every workload.
