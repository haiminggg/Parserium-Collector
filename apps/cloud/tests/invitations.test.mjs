import assert from 'node:assert/strict';
import {test} from 'node:test';
import {runtime,migrate} from './runtime.mjs';
import {issueInvitation} from '../src/invitations.mjs';
import {digest} from '../src/security.mjs';

async function setup(t) {
  const mf=await runtime(); t.after(()=>mf.dispose());
  const db=await mf.getD1Database('DB'); await migrate(db);
  await db.prepare("INSERT INTO workspaces VALUES ('w1','Test workspace',1)").run();
  return db;
}
test('operator invitation returns random secret once and stores only digest with bounded expiry',async t=>{
  const db=await setup(t);
  const issued=await issueInvitation(db,{workspaceId:'w1',email:'person@example.test'},100);
  assert.match(issued.token,/^[a-f0-9]{64}$/);
  const row=await db.prepare('SELECT * FROM invitations').first();
  assert.equal(row.token_digest,await digest(issued.token));
  assert.equal(row.expires_at,86500);
  assert.equal(row.role,'member');
  assert.equal(JSON.stringify(row).includes(issued.token),false);
  const second=await issueInvitation(db,{workspaceId:'w1',email:'person@example.test'},100);
  assert.notEqual(second.token,issued.token);
});
test('invalid operator input never creates an invitation',async t=>{
  const db=await setup(t);
  for (const input of [
    {workspaceId:'missing',email:'person@example.test'},
    {workspaceId:'w1',email:'not-an-email'},
    {workspaceId:'w1',email:'person@example.test\n'},
    {workspaceId:'w1',email:'person@example.test',role:'admin'},
    {workspaceId:'w1',email:'person@example.test',ttlSeconds:0},
    {workspaceId:'w1',email:'person@example.test',ttlSeconds:604801},
  ]) await assert.rejects(()=>issueInvitation(db,input,100));
  assert.equal((await db.prepare('SELECT count(*) AS n FROM invitations').first()).n,0);
});
