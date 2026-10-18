# Audit finding: PostgreSQL integration test environment

**Tracking:** [GitHub issue #526](https://github.com/FlossWare/claude-ensemble/issues/526)  
**Status:** Open  
**Priority:** Test reliability.

## Description
When running `npm test`, `learning/test_dcab_critical.cjs` connects to PostgreSQL using `PGHOST` / `PGPORT` and related environment variables, defaulting to `127.0.0.1:5432`. A machine without the expected database receives a connection failure.

## Triage guidance
Do not automatically mock or silently skip this integration test when PostgreSQL is unavailable. Separate environment-independent unit tests from database integration tests; provision PostgreSQL in CI for the required integration job, and make any intentionally omitted integration run explicit and visible.

See [issue #526](https://github.com/FlossWare/claude-ensemble/issues/526) for acceptance criteria.
