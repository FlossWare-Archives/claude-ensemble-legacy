const assert = require('node:assert/strict');
const { test } = require('node:test');
const { Pool } = require('pg');

const config = {
  host: process.env.PGHOST || '127.0.0.1',
  port: Number(process.env.PGPORT || 5432),
  database: process.env.PGDATABASE || 'learning',
  user: process.env.PGUSER || process.env.USER,
  password: process.env.PGPASSWORD,
  connectionTimeoutMillis: 10000,
  max: 2,
};

test('PostgreSQL integration: pool connects and supports a transaction round trip', async () => {
  const pool = new Pool(config);
  let client;

  try {
    try {
      client = await pool.connect();
    } catch (error) {
      throw new Error(
        `Could not connect to PostgreSQL at ${config.host}:${config.port}/${config.database} as ${config.user}. Start/configure PostgreSQL and set PGHOST, PGPORT, PGDATABASE, PGUSER, and PGPASSWORD as needed.`,
        { cause: error },
      );
    }

    await client.query('BEGIN');
    const result = await client.query('SELECT 1 AS test');
    assert.deepEqual(result.rows, [{ test: 1 }]);
    await client.query('ROLLBACK');
  } finally {
    if (client) {
      try {
        await client.query('ROLLBACK');
      } catch {
        // Preserve the original test failure; this is best-effort cleanup.
      }
      client.release();
    }
    await pool.end();
  }
});
