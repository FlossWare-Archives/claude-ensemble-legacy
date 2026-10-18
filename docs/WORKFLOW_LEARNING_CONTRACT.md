# Workflow learning contract

## Supported path

Supported workflow skills submit evaluated outcomes through the shared bridge:

`shared/learning-outcome-bridge.js` -> `tools/record_workflow_outcome.py` -> Learning service.

The adapter requires a stable outcome identity, a rating, token/cost values, and explicit evidence. The Learning service owns artifact persistence, outcome deduplication, and Thompson updates. A successful process exit by itself is not a learning signal.

## Embeddings

Semantic embeddings are not required by the supported workflow-outcome contract. Search components may use embeddings for their own retrieval indexes, but outcome recording does not generate them or promise a vector column.

## Legacy PostgreSQL hook

`hooks/workflow-completion-hook.js` is deprecated and is not registered by `settings.json.default`. It directly references an external `workflows.*` schema whose migrations and consumers are not defined in this repository. Its historical `embedding` field must not be treated as a current contract. Do not re-enable the module until the owning schema, nullability, and consumers are available and tested.

The supported contract is covered by `tests/test_workflow_learning_bridge_contract.py`.
