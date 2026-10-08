import assert from 'node:assert/strict';
import {test} from 'node:test';
import {runtime,migrate} from './runtime.mjs';
import * as operator from '../scripts/operator.mjs';

test('operator creates a workspace and issues an email-bound invitation using real local D1',async t=>{
  assert.equal(typeof operator.runOperator,'function');
  const mf=await runtime(); t.after(()=>mf.dispose());
  const db=await mf.getD1Database('DB'); await migrate(db);
  const created=await operator.runOperator(db,['workspace-create','--name','Local test workspace'],100);
  assert.match(created.workspaceId,/^[a-f0-9-]{36}$/);
  const issued=await operator.runOperator(db,['invite','--workspace-id',created.workspaceId,'--email','operator@example.test'],100);
  assert.match(issued.token,/^[a-f0-9]{64}$/);
  const row=await db.prepare('SELECT * FROM invitations').first();
  assert.equal(row.email,'operator@example.test');
  assert.equal(row.role,'member');
  assert.equal(row.expires_at,86500);
});
test('operator rejects unknown flags and commands before accessing a database',async()=>{
  assert.equal(typeof operator.runOperator,'function');
  for(const args of [[],['delete'],['workspace-create','--name',''],['workspace-create','--name','x','--oops','y'],['invite','--email','x']]) {
    await assert.rejects(()=>operator.runOperator(null,args,100),/Invalid operator arguments/);
  }
});
