export const meta = {
  name: 'memory-rag-search',
  description: 'Semantic search across all memories using RAG',
  whenToUse: 'When you need to find relevant memories by meaning, not just keywords',
  phases: [
    { title: 'Semantic Search', detail: 'Query ChromaDB with embeddings' },
    { title: 'Retrieve Content', detail: 'Load full memory content' },
    { title: 'Synthesize', detail: 'Multi-AI consensus on relevance' },
  ],
}

const query = args?.query || args

if (!query) {
  log('❌ No query provided')
  log('Usage: claude run memory-rag-search query="your search query"')
  return { status: 'error', message: 'No query provided' }
}

log('═'.repeat(60))
log('🔍 MEMORY RAG SEARCH')
log('═'.repeat(60))
log(`Query: "${query}"`)
log('')

const MEMORY_DIR = `${process.env.HOME}/.claude/memory`
const CHROMA_COLLECTION = 'claude-memories'

// This legacy hook has no repository-wide multi-ai-config.json contract. Allow
// per-invocation configuration instead of silently depending on a nonexistent file.
// Four workers remain the default; worker_count selects the first N configured workers.
const DEFAULT_WORKER_COUNT = 4;
const MAX_WORKER_COUNT = 4;

function resolveWorkerCount(value) {
  if (value === undefined || value === null || value === '') {
    return DEFAULT_WORKER_COUNT;
  }

  const parsed = typeof value === 'number'
    ? value
    : (typeof value === 'string' && /^\d+$/.test(value) ? Number(value) : NaN);

  if (!Number.isInteger(parsed) || parsed < 1 || parsed > MAX_WORKER_COUNT) {
    log(`⚠️ Invalid worker_count "${String(value)}"; using safe default ${DEFAULT_WORKER_COUNT} (valid range: 1-${MAX_WORKER_COUNT}).`);
    return DEFAULT_WORKER_COUNT;
  }
  return parsed;
}

const workerCount = resolveWorkerCount(
  args && typeof args === 'object' ? args.worker_count : undefined
);

// PHASE 1: Semantic search
phase('Semantic Search')

log('🔎 Searching memories with semantic similarity...')

const searchResults = await agent(`Semantic search in ChromaDB for: "${query}"

Python code:

import chromadb
import json

client = chromadb.PersistentClient(path=f"{process.env.HOME}/.claude/chroma")

try:
    collection = client.get_collection("${CHROMA_COLLECTION}")
except:
    print("❌ Memory index not found. Run memory-rag-index first.")
    exit(1)

# Semantic search - get top 10 results
results = collection.query(
    query_texts=["${query}"],
    n_results=10
)

# Format results
memories = []
for doc, meta, distance in zip(
    results['documents'][0],
    results['metadatas'][0],
    results['distances'][0]
):
    memories.append({
        'name': meta['name'],
        'filename': meta['filename'],
        'description': meta['description'],
        'type': meta['type'],
        'topics': meta.get('topics', '').split(',') if meta.get('topics') else [],
        'distance': float(distance),
        'relevance_score': max(0, 1 - distance)  # Convert distance to 0-1 score
    })

# Sort by relevance
memories.sort(key=lambda x: x['relevance_score'], reverse=True)

print(json.dumps({
    'query': "${query}",
    'results': memories,
    'count': len(memories)
}, indent=2))
`, {
  label: 'semantic-search',
  schema: {
    type: 'object',
    properties: {
      query: { type: 'string' },
      results: {
        type: 'array',
        items: {
          type: 'object',
          properties: {
            name: { type: 'string' },
            filename: { type: 'string' },
            description: { type: 'string' },
            type: { type: 'string' },
            topics: { type: 'array', items: { type: 'string' } },
            distance: { type: 'number' },
            relevance_score: { type: 'number' }
          }
        }
      },
      count: { type: 'number' }
    }
  }
})

log(`✅ Found ${searchResults.count} relevant memories`)

// PHASE 2: Retrieve top results
phase('Retrieve Content')

log('')
log('📖 Loading full content for top 5 results...')

const topResults = searchResults.results.slice(0, 5)

const fullMemories = await parallel(topResults.map(r => () =>
  agent(`Read full content of memory: ${MEMORY_DIR}/${r.filename}

Return the complete memory content.`, {
    label: `read-${r.filename.replace(/\\.md$/, '')}`,
    schema: {
      type: 'object',
      properties: {
        filename: { type: 'string' },
        content: { type: 'string' }
      }
    }
  }).then(result => ({
    ...r,
    full_content: result?.content || ''
  }))
))

log(`✅ Retrieved ${fullMemories.filter(Boolean).length} full memories`)

// PHASE 3: Multi-AI synthesis with arbiter/worker pattern
phase('Synthesize')

log('')
log(`🤖 Multi-AI consensus on relevance (${workerCount} workers selected from opus/sonnet/haiku/gemini)...`)

const memoriesContext = fullMemories.filter(Boolean).map((m, i) => `
${i + 1}. ${m.name} (${m.type})
   Relevance: ${(m.relevance_score * 100).toFixed(1)}%
   Topics: ${m.topics.join(', ')}
   Description: ${m.description}

Content:
${m.full_content.slice(0, 500)}...
`).join('\n')

log('📝 Phase 1: Workers analyze relevance...')

const workers = await parallel([
  () => agent(`[OPUS WORKER] Analyze memories for relevance to: "${query}"

Memories:
${memoriesContext}

Task:
1. Rate each memory's relevance (0-100)
2. Identify TOP 3 most relevant
3. Key insights from top 3
4. Related topics to explore

Return structured analysis.`, {
    label: 'opus-worker',
    model: 'opus',
    schema: {
      type: 'object',
      properties: {
        model: { type: 'string' },
        top_memories: {
          type: 'array',
          items: {
            type: 'object',
            properties: {
              name: { type: 'string' },
              relevance_rating: { type: 'number' },
              why_relevant: { type: 'string' }
            }
          }
        },
        key_insights: { type: 'array', items: { type: 'string' } },
        related_topics: { type: 'array', items: { type: 'string' } }
      }
    }
  }),

  () => agent(`[SONNET WORKER] Analyze memories for relevance to: "${query}"

Memories:
${memoriesContext}

Task:
1. Rate each memory's relevance (0-100)
2. Identify TOP 3 most relevant
3. Key insights from top 3
4. Related topics to explore

Return structured analysis.`, {
    label: 'sonnet-worker',
    model: 'sonnet',
    schema: {
      type: 'object',
      properties: {
        model: { type: 'string' },
        top_memories: {
          type: 'array',
          items: {
            type: 'object',
            properties: {
              name: { type: 'string' },
              relevance_rating: { type: 'number' },
              why_relevant: { type: 'string' }
            }
          }
        },
        key_insights: { type: 'array', items: { type: 'string' } },
        related_topics: { type: 'array', items: { type: 'string' } }
      }
    }
  }),

  () => agent(`[HAIKU WORKER] Analyze memories for relevance to: "${query}"

Memories:
${memoriesContext}

Task:
1. Rate each memory's relevance (0-100)
2. Identify TOP 3 most relevant
3. Key insights from top 3
4. Related topics to explore

Return structured analysis.`, {
    label: 'haiku-worker',
    model: 'haiku',
    schema: {
      type: 'object',
      properties: {
        model: { type: 'string' },
        top_memories: {
          type: 'array',
          items: {
            type: 'object',
            properties: {
              name: { type: 'string' },
              relevance_rating: { type: 'number' },
              why_relevant: { type: 'string' }
            }
          }
        },
        key_insights: { type: 'array', items: { type: 'string' } },
        related_topics: { type: 'array', items: { type: 'string' } }
      }
    }
  }),

  () => agent(`[GEMINI WORKER] Analyze memories for relevance to: "${query}"

Memories:
${memoriesContext}

Task:
1. Rate each memory's relevance (0-100)
2. Identify TOP 3 most relevant
3. Key insights from top 3
4. Related topics to explore

Return structured analysis.`, {
    label: 'gemini-worker',
    agentType: 'gemini',
    schema: {
      type: 'object',
      properties: {
        model: { type: 'string' },
        top_memories: {
          type: 'array',
          items: {
            type: 'object',
            properties: {
              name: { type: 'string' },
              relevance_rating: { type: 'number' },
              why_relevant: { type: 'string' }
            }
          }
        },
        key_insights: { type: 'array', items: { type: 'string' } },
        related_topics: { type: 'array', items: { type: 'string' } }
      }
    }
  }),
].slice(0, workerCount))

const validWorkers = workers.filter(Boolean)
log(`✅ ${validWorkers.length} workers completed`)

log('⚖️  Phase 2: Arbiter selects best analysis...')

const synthesis = await agent(`[ARBITER] Review ${validWorkers.length} worker analyses and select the best synthesis.

Query: "${query}"

Worker Proposals:
${validWorkers.map((w, i) => `
Worker ${i + 1} (${w.model || 'unknown'}):
Top Memories: ${w.top_memories.map(m => `${m.name} (${m.relevance_rating}%)`).join(', ')}
Insights: ${w.key_insights.join('; ')}
Related: ${w.related_topics.join(', ')}
`).join('\n')}

Task:
1. Evaluate each worker's analysis
2. Select the BEST analysis (most accurate relevance ratings, most useful insights)
3. Return the winning analysis with attribution
4. Explain why you chose it

Return structured decision.`, {
  label: 'arbiter-select',
  schema: {
    type: 'object',
    properties: {
      query: { type: 'string' },
      winning_worker: { type: 'string' },
      why_selected: { type: 'string' },
      top_memories: {
        type: 'array',
        items: {
          type: 'object',
          properties: {
            name: { type: 'string' },
            filename: { type: 'string' },
            relevance_rating: { type: 'number' },
            why_relevant: { type: 'string' }
          }
        }
      },
      key_insights: { type: 'array', items: { type: 'string' } },
      related_topics: { type: 'array', items: { type: 'string' } }
    }
  }
})

log(`✅ Arbiter selected: ${synthesis.winning_worker}`)
log(`   Reason: ${synthesis.why_selected}`)

// Display results
log('')
log('═'.repeat(60))
log('🎯 SEARCH RESULTS')
log('═'.repeat(60))
log(`Query: "${query}"`)
log(`Found: ${searchResults.count} memories`)
log('')
log('📌 Top 3 Most Relevant:')
synthesis.top_memories.slice(0, 3).forEach((m, i) => {
  log(`${i + 1}. ${m.name}`)
  log(`   File: ${m.filename}`)
  log(`   Relevance: ${m.relevance_rating}%`)
  log(`   Why: ${m.why_relevant}`)
  log('')
})

log('💡 Key Insights:')
synthesis.key_insights.forEach(insight => {
  log(`  • ${insight}`)
})
log('')

log('🔗 Related Topics to Explore:')
synthesis.related_topics.forEach(topic => {
  log(`  • ${topic}`)
})

log('═'.repeat(60))

return {
  status: 'success',
  query: query,
  total_found: searchResults.count,
  top_memories: synthesis.top_memories,
  key_insights: synthesis.key_insights,
  related_topics: synthesis.related_topics,
  all_results: searchResults.results
}
