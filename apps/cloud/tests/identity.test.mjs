import assert from 'node:assert/strict';
import {test} from 'node:test';
import {createHash} from 'node:crypto';
import {runtime, migrate} from './runtime.mjs';
import {completeIdentity} from '../src/identity.mjs';
const hash = value => createHash('sha256').update(value).digest('hex');
// Test-only inputs to the internal admission layer, not forged Google tokens or an HTTP bypass.
const claims = {iss:'https://accounts.google.com',sub:'subject-one',email:'one@example.test',email_verified:true};
async function setup(t) {
  const mf = await runtime(); t.after(() => mf.dispose());
  const db = await mf.getD1Database('DB'); await migrate(db);
  await db.prepare("INSERT INTO workspaces VALUES ('w1','Test workspace',1)").run();
  await db.prepare("INSERT INTO invitations VALUES (?,'w1','one@example.test','owner',1,500,NULL,NULL)").bind(hash('invitation')).run();
  return db;
}
test('invited verified identity receives a hashed 24-hour session and membership', async t => {
  const db = await setup(t);
  const issued = await completeIdentity(db,claims,hash('invitation'),100);
  assert.ok(issued);
  assert.match(issued.token,/^[a-f0-9]{64}$/);
  assert.match(issued.csrf,/^[a-f0-9]{64}$/);
  const stored = await db.prepare('SELECT * FROM sessions').first();
  assert.equal(stored.token_digest,hash(issued.token));
  assert.equal(stored.csrf_digest,hash(issued.csrf));
  assert.equal(stored.expires_at,86500);
  assert.equal((await db.prepare('SELECT * FROM memberships').all()).results.length,1);
});
test('returning user is keyed by issuer/subject; matching email alone never grants access', async t => {
  const db = await setup(t);
  const first = await completeIdentity(db,claims,hash('invitation'),100);
  assert.ok(first);
  assert.ok(await completeIdentity(db,{...claims,email:'changed@example.test'},null,101));
  assert.equal(await completeIdentity(db,{...claims,sub:'different-subject'},null,102),null);
  assert.equal((await db.prepare('SELECT * FROM users').all()).results.length,1);
});
test('unverified, uninvited and expired invitations cannot create sessions', async t => {
  const db = await setup(t);
  assert.equal(await completeIdentity(db,{...claims,email_verified:false},hash('invitation'),100),null);
  assert.equal(await completeIdentity(db,claims,null,100),null);
  assert.equal(await completeIdentity(db,claims,hash('invitation'),500),null);
  assert.equal((await db.prepare('SELECT * FROM sessions').all()).results.length,0);
});
test('revoked membership cannot be restored by a spent invitation', async t => {
  const db = await setup(t);
  assert.ok(await completeIdentity(db,claims,hash('invitation'),100));
  await db.prepare('DELETE FROM memberships').run();
  assert.equal(await completeIdentity(db,claims,null,101),null);
  assert.equal(await completeIdentity(db,claims,hash('invitation'),101),null);
});
