# Audit finding: PostgreSQL integration test environment

**Tracking:** [GitHub issue #526](https://github.com/FlossWare/claude-ensemble/issues/526)  
**Status:** Resolved  
**Priority:** Keep integration coverage required while separating it from local unit tests.

## Resolution
- `npm test` now runs environment-independent JavaScript unit tests and syntax checks.
- `npm run test:integration:postgres` runs the real PostgreSQL connection/transaction round-trip test.
- GitHub Actions provisions PostgreSQL and runs the integration test as a separate required step.
- GitLab CI has a dedicated PostgreSQL service job for the integration test.
- Connection failures identify the configured host, port, database, and user without printing the password; the underlying driver error is preserved as the cause.
- Local commands and environment variables are documented in `learning/README.md`.

Implementation: [PR #538](https://github.com/FlossWare/claude-ensemble/pull/538). CI passed for the GitHub Actions integration and non-GA test workflows.

## Safety
No silent skip or mock fallback was added to the PostgreSQL integration test.
