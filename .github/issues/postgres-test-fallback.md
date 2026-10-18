# Issue: [Jules] Handle missing PostgreSQL database gracefully in node test runner

**Status:** OPEN
**Priority:** Low

## Description
When running `npm test`, `learning/test_dcab_critical.cjs` attempts to establish a direct connection to a local PostgreSQL database (`127.0.0.1:5432`). If a local PostgreSQL instance is not running, the test fails with `ECONNREFUSED`.

## Proposed Solution
- Add connection detection or mock fallbacks to `learning/test_dcab_critical.cjs` so that tests skip or run in mock mode when PostgreSQL is unreachable.
