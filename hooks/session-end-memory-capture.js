#!/usr/bin/env node

/**
 * Canonical SessionEnd capture adapter.
 *
 * Captures only stable, non-transcript session metadata. It never reads the
 * transcript, triggers Learning, mutates learner state, or promotes Knowledge.
 * Memory delivery is idempotent through append-once; a metadata-only session
 * No transcript or cross-session message bus is involved.
 */

const MEMORY_URL = process.env.FLOSSWARE_MEMORY_URL || 'http://127.0.0.1:8767';
const REQUEST_TIMEOUT_MS = Number.parseInt(process.env.FLOSSWARE_MEMORY_TIMEOUT_MS || '1500', 10);
const EVENT_NAME = 'claude_code.session_end';

function readEvent() {
  return new Promise((resolve, reject) => {
    const chunks = [];
    process.stdin.on('data', chunk => chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk)));
    process.stdin.on('error', reject);
    process.stdin.on('end', () => {
      try {
        const input = Buffer.concat(chunks).toString('utf8');
        resolve(input.trim() ? JSON.parse(input) : {});
      } catch {
        reject(new Error('invalid Claude Code hook JSON'));
      }
    });
  });
}

async function eventId(sessionId) {
  // Dynamic import works both inside the repository's ESM package scope and
  // when the installer copies this .js file into ~/.claude/hooks (CommonJS).
  const { createHash } = await import('node:crypto');
  const digest = createHash('sha256').update(sessionId, 'utf8').digest('hex');
  return `claude-code:session-end:${digest}`;
}

async function capture(event) {
  const sessionId = typeof event?.session_id === 'string' ? event.session_id.trim() : '';
  if (!sessionId) {
    process.stderr.write('Memory capture skipped: SessionEnd event has no session_id\n');
    return;
  }

  // Keep the entry deterministic so retries have the same payload digest.
  const entry = {
    event_type: EVENT_NAME,
    hook_event_name: 'SessionEnd',
    session_id: sessionId,
    source: 'claude-code',
  };
  const id = await eventId(sessionId);
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    const endpoint = new URL('/memory/append-once', MEMORY_URL.endsWith('/') ? MEMORY_URL : `${MEMORY_URL}/`);
    const response = await fetch(endpoint, {
      method: 'POST',
      headers: { 'content-type': 'application/json', accept: 'application/json' },
      body: JSON.stringify({ name: 'session_events', event_id: id, entry }),
      signal: controller.signal,
    });
    let result;
    try {
      result = await response.json();
    } catch {
      throw new Error(`Memory service returned invalid JSON (HTTP ${response.status})`);
    }
    if (!response.ok || result?.ok !== true || !['stored', 'duplicate'].includes(result?.status)) {
      throw new Error(result?.error || `Memory service rejected capture (HTTP ${response.status})`);
    }
    if (result.event_id !== id) {
      throw new Error('Memory service acknowledgement event_id does not match submitted event');
    }
  } finally {
    clearTimeout(timeout);
  }
}

async function main() {
  let event;
  try {
    event = await readEvent();
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    process.stderr.write(`SessionEnd event unavailable: ${message}\n`);
    return;
  }

  const sessionId = typeof event?.session_id === 'string' ? event.session_id.trim() : '';
  if (!sessionId) {
    process.stderr.write('SessionEnd event has no session_id; Memory capture skipped\n');
    return;
  }

  try {
    await capture(event);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    process.stderr.write(`Memory capture unavailable: ${message}\n`);
  }
}

main().catch(error => {
  const message = error instanceof Error ? error.message : String(error);
  process.stderr.write(`Memory capture unavailable: ${message}\n`);
});
