# Learning service test commands

## Environment-independent JavaScript tests

Run the unit tests and syntax checks without a PostgreSQL instance:

```bash
npm test
```

## PostgreSQL integration test

`learning/test_dcab_critical.cjs` is a real database integration test. It checks a connection, executes a transaction round trip, and rolls the transaction back. It is intentionally not mocked or silently skipped.

Start a local PostgreSQL instance and configure a test database/user, then set the connection variables for that test environment:

```bash
export PGHOST=127.0.0.1
export PGPORT=5432
export PGDATABASE=learning
export PGUSER=ensemble
export PGPASSWORD='your-local-test-password'
npm run test:integration:postgres
```

Use credentials for a disposable test database, not a production database. CI provisions PostgreSQL and runs this integration test separately from `npm test`. Connection failures report the selected host, port, database, and user, and preserve the underlying driver error.
