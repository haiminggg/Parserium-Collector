import assert from 'node:assert/strict';
import {test} from 'node:test';
import {createHash} from 'node:crypto';
import {runtime,migrate} from './runtime.mjs';

const token = 'a'.repeat(64), csrf = 'b'.repeat(64);
const digest = value => createHash('sha256').update(value).digest('hex');
async function setup(t) {
  const mf = await runtime();
  t.after(() => mf.dispose());
  const db = await mf.getD1Database('DB');
  await migrate(db);
  // Test-only records in the actual local D1 runtime, never production seed data.
  await db.batch([
    db.prepare("INSERT INTO users VALUES ('u1','https://accounts.google.com','test-subject','test@example.test',1)"),
    db.prepare("INSERT INTO workspaces VALUES ('w1','Test one',1),('w2','Test two',1)"),
    db.prepare("INSERT INTO memberships VALUES ('w1','u1','owner')"),
    db.prepare("INSERT INTO documents (id,workspace_id,filename,size_bytes,created_at) VALUES ('d1','w1','one.pdf',100,1),('d2','w2','private.pdf',100,1)"),
    db.prepare('INSERT INTO sessions VALUES (?,?,?,?,?)').bind(digest(token), 'u1', digest(csrf), Math.floor(Date.now()/1000), Math.floor(Date.now()/1000)+3600),
  ]);
  const request = (path, options = {}) => mf.dispatchFetch(`https://cloud.test/api/cloud/v1${path}`, {...options, headers: {Cookie: `__Host-parserium_cloud=${token}`, ...options.headers}});
  return {db, request};
}

test('session returns identity and only member workspaces', async t => {
  const {request} = await setup(t);
  const response = await request('/session');
  assert.equal(response.status, 200);
  const body = await response.json();
  assert.equal(body.user.id, 'u1');
  assert.deepEqual(body.workspaces.map(w => w.id), ['w1']);
  assert.equal(JSON.stringify(body).includes(digest(token)), false);
});
test('document list and lookup enforce workspace membership', async t => {
  const {db,request} = await setup(t);
  await db.prepare(`INSERT INTO parse_jobs
    (id,workspace_id,document_id,request_id,status,attempt_count,day_start,reserved_runtime_ms,runtime_ms,created_at,updated_at)
    VALUES ('11111111-1111-4111-8111-111111111111','w1','d1','22222222-2222-4222-8222-222222222222','running',1,0,120000,0,1,2)`).run();
  const response = await request('/documents?workspace=w1');
  assert.equal(response.status, 200);
  const documents=(await response.json()).documents;
  assert.deepEqual(documents.map(d => d.id), ['d1']);
  assert.equal(documents[0].job_id,'11111111-1111-4111-8111-111111111111');
  assert.equal(documents[0].job_status,'running');
  assert.equal((await request('/documents?workspace=w2')).status, 404);
  assert.equal((await request('/documents/d2')).status, 404);
  const lookup=await request('/documents/d1');assert.equal(lookup.status, 200);
  assert.equal((await lookup.json()).document.job_status,'running');
});
test('expired sessions are rejected', async t => {
  const {db, request} = await setup(t);
  await db.prepare('UPDATE sessions SET created_at=1, expires_at=2').run();
  assert.equal((await request('/session')).status, 401);
});
test('logout requires origin and CSRF, then revokes the stored session', async t => {
  const {request} = await setup(t);
  assert.equal((await request('/logout', {method: 'POST'})).status, 403);
  assert.equal((await request('/logout', {method: 'POST', headers: {Origin: 'https://evil.test', 'X-CSRF-Token': csrf}})).status, 403);
  assert.equal((await request('/logout', {method: 'POST', headers: {Origin: 'https://cloud.test', 'X-CSRF-Token': 'wrong'}})).status, 403);
  const response = await request('/logout', {method: 'POST', headers: {Origin: 'https://cloud.test', 'X-CSRF-Token': csrf}});
  assert.equal(response.status, 204);
  assert.match(response.headers.get('Set-Cookie'), /Max-Age=0/);
  assert.equal((await request('/session')).status, 401);
});
test('removing membership immediately removes document access', async t => {
  const {db, request} = await setup(t);
  await db.prepare('DELETE FROM memberships').run();
  assert.equal((await request('/documents/d1')).status, 404);
});

test('unknown and duplicate session cookies are rejected', async t => {
  const {request} = await setup(t);
  assert.equal((await request('/session', {headers: {Cookie: `__Host-parserium_cloud=${'c'.repeat(64)}`}})).status, 401);
  assert.equal((await request('/session', {headers: {Cookie: `__Host-parserium_cloud=${token}; __Host-parserium_cloud=${token}`}})).status, 401);
});

test('session lifetime constraint rejects more than 24 hours', async t => {
  const {db} = await setup(t);
  await assert.rejects(db.prepare('UPDATE sessions SET expires_at=created_at+86401').run());
});

test('metadata requires an existing workspace and bounded file size', async t => {
  const {db} = await setup(t);
  await assert.rejects(db.prepare("INSERT INTO documents VALUES ('bad','missing','bad.pdf',10,1)").run());
  await assert.rejects(db.prepare("INSERT INTO documents VALUES ('large','w1','large.pdf',10485761,1)").run());
});
