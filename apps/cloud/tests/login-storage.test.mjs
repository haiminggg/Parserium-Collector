import assert from 'node:assert/strict';
import {test} from 'node:test';
import {readFile} from 'node:fs/promises';
import {runtime} from './runtime.mjs';
import {consumeLogin, redeemInvitation} from '../src/login-storage.mjs';

async function setup(t) {
  const mf = await runtime();
  t.after(() => mf.dispose());
  const db = await mf.getD1Database('DB');
  for (const file of ['0001_identity.sql','0002_login_transactions.sql']) {
    for (const sql of (await readFile(`migrations/${file}`, 'utf8')).split(';').filter(s => s.trim())) await db.prepare(sql).run();
  }
  await db.batch([
    db.prepare("INSERT INTO users VALUES ('u1','google','one','one@example.test',1),('u2','google','two','two@example.test',1)"),
    db.prepare("INSERT INTO workspaces VALUES ('w1','Test',1)"),
    db.prepare("INSERT INTO invitations VALUES (?,'w1','one@example.test','member',1,500,NULL,NULL)").bind('i'.repeat(64)),
    db.prepare('INSERT INTO login_transactions VALUES (?,?,?,?,NULL,1,500)').bind('b'.repeat(64),'s'.repeat(64),'test-nonce','test-verifier'),
  ]);
  return db;
}
test('login state is browser-bound and single use even with simultaneous callbacks', async t => {
  const db = await setup(t);
  assert.equal(await consumeLogin(db,'wrong','s'.repeat(64),100), null);
  assert.equal(await consumeLogin(db,'b'.repeat(64),'wrong',100), null);
  const results = await Promise.all([consumeLogin(db,'b'.repeat(64),'s'.repeat(64),100),consumeLogin(db,'b'.repeat(64),'s'.repeat(64),100)]);
  assert.equal(results.filter(Boolean).length,1);
  assert.equal(results.find(Boolean).nonce,'test-nonce');
});
test('expired login state cannot be consumed', async t => {
  const db = await setup(t);
  assert.equal(await consumeLogin(db,'b'.repeat(64),'s'.repeat(64),500),null);
});
test('invitation admits only its bound user email and cannot be reused', async t => {
  const db = await setup(t);
  assert.equal(await redeemInvitation(db,'i'.repeat(64),'u2',100),false);
  assert.equal(await redeemInvitation(db,'i'.repeat(64),'u1',100),true);
  assert.equal(await redeemInvitation(db,'i'.repeat(64),'u1',100),false);
  const membership = await db.prepare('SELECT * FROM memberships').all();
  assert.deepEqual(membership.results,[{workspace_id:'w1',user_id:'u1',role:'member'}]);
});
test('expired invitation cannot grant membership', async t => {
  const db = await setup(t);
  assert.equal(await redeemInvitation(db,'i'.repeat(64),'u1',500),false);
  assert.equal((await db.prepare('SELECT * FROM memberships').all()).results.length,0);
});
test('simultaneous invitation redemption has one winner', async t => {
  const db = await setup(t);
  const results = await Promise.all([redeemInvitation(db,'i'.repeat(64),'u1',100),redeemInvitation(db,'i'.repeat(64),'u1',100)]);
  assert.equal(results.filter(Boolean).length,1);
  assert.equal((await db.prepare('SELECT * FROM memberships').all()).results.length,1);
});
