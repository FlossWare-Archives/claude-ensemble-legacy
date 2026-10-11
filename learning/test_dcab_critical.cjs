const assert = require('node:assert/strict');
const { test } = require('node:test');
const { Pool } = require('pg');

test('PostgreSQL pool connects and supports a transaction round trip', async () => {
  const pool = new Pool({
    host: process.env.PGHOST || '127.0.0.1',
    port: Number(process.env.PGPORT || 5432),
    database: process.env.PGDATABASE || 'learning',
    user: process.env.PGUSER || process.env.USER,
    password: process.env.PGPASSWORD,
    connectionTimeoutMillis: 5000,
    max: 2,
  });

  try {
    const client = await pool.connect();
    try {
      await client.query('BEGIN');
      const result = await client.query('SELECT 1 AS test');
      assert.deepEqual(result.rows, [{ test: 1 }]);
      await client.query('ROLLBACK');
    } finally {
      client.release();
    }
  } finally {
    await pool.end();
  }
});
