import assert from 'node:assert/strict';
import {test} from 'node:test';
import {readFile} from 'node:fs/promises';
import {runtime} from './runtime.mjs';

test('Worker bundle exports the parser Container Durable Object class', async () => {
  const source = await readFile('src/worker.mjs', 'utf8');
  assert.match(source, /export\s*\{\s*ParserContainer\s*\}/);
  const mf = await runtime();
  await mf.dispose();
});

test('migration creates identity, session and workspace metadata tables', async () => {
  const mf = await runtime();
  try {
    const db = await mf.getD1Database('DB');
    const sql = await readFile('migrations/0001_identity.sql', 'utf8');
    for (const statement of sql.split(';').filter(s => s.trim() && !/^\s*--[^\n]*\s*$/.test(s))) await db.prepare(statement).run();
    const result = await db.prepare("SELECT name FROM sqlite_master WHERE type = 'table'").all();
    for (const name of ['users', 'workspaces', 'memberships', 'sessions', 'documents']) {
      assert.ok(result.results.some(row => row.name === name), `Missing ${name}`);
    }
  } finally { await mf.dispose(); }
});

test('protected cloud API fails closed without a session', async () => {
  const mf = await runtime();
  try {
    const response = await mf.dispatchFetch('https://cloud.test/api/cloud/v1/documents');
    assert.equal(response.status, 401);
    assert.equal(response.headers.get('Cache-Control'), 'no-store');
  } finally { await mf.dispose(); }
});

test('public health reports service and processing separately without resource details', async () => {
  const mf = await runtime({PROCESSING_ENABLED:'false'});
  try {
    const response = await mf.dispatchFetch('https://cloud.test/api/cloud/v1/health');
    assert.equal(response.status, 200);
    assert.deepEqual(await response.json(), {ok:true,processing:'disabled'});
    assert.equal(response.headers.get('Cache-Control'), 'no-store');
  } finally { await mf.dispose(); }
});
