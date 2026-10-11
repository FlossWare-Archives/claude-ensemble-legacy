# Issue: [Jules] Make worker count configurable in RAG memory search hooks

**Status:** OPEN
**Priority:** Low

## Description
In `hooks/memory-rag-search.js`, the worker count for RAG semantic search is hardcoded or noted as a TODO comment (`// TODO: Load multi-ai-config.json to make worker count configurable`).

## Proposed Solution
- Parse `multi-ai-config.json` or environment variables to configure RAG search worker counts dynamically.
