# Audit finding: PostgreSQL integration test environment

**Tracking:** [GitHub issue #526](https://github.com/FlossWare/claude-ensemble/issues/526)  
**Status:** In progress  
**Priority:** Keep integration coverage required while separating it from local unit tests.

## Description
`learning/test_dcab_critical.cjs` verifies a real PostgreSQL connection and transaction round trip. It must not silently skip or mock database access when PostgreSQL is unavailable.

## Resolution approach
- `npm test` runs environment-independent JavaScript unit tests and syntax checks.
- `npm run test:integration:postgres` runs the real PostgreSQL integration test.
- GitHub Actions provisions PostgreSQL and runs the integration test in a dedicated required step.
- GitLab CI has a dedicated PostgreSQL service job for the same integration test.
- Connection errors include the configured host, port, database, and user (never the password), with the original error retained as the cause.

## Local integration test
Start PostgreSQL, create/configure a test database, then set `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, and `PGPASSWORD` for that test environment and run `npm run test:integration:postgres`. See `learning/README.md`.

## Source
Audit recorded in commit [3462918](https://github.com/FlossWare/claude-ensemble/commit/34629180fd9d66e13c546175f9c6eece4ddd20c8).
