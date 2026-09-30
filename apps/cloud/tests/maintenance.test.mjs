import assert from 'node:assert/strict';
import {test} from 'node:test';
import {runtime,migrate} from './runtime.mjs';
import {cleanupAuth} from '../src/maintenance.mjs';

test('cleanup removes at most 100 expired records per table and preserves live authentication',async t=>{
  const mf=await runtime(); t.after(()=>mf.dispose());
  const db=await mf.getD1Database('DB'); await migrate(db);
  await db.prepare("INSERT INTO users VALUES ('u','google','test','test@example.test',1)").run();
  for(let start=0;start<105;start+=25){
    await db.batch(Array.from({length:Math.min(25,105-start)},(_,i)=>
      db.prepare('INSERT INTO sessions VALUES (?,?,?,?,?)').bind((start+i).toString(16).padStart(64,'0'),'u','c'.repeat(64),1,2)));
  }
  await db.prepare('INSERT INTO sessions VALUES (?,?,?,?,?)').bind('f'.repeat(64),'u','c'.repeat(64),1,1000).run();
  await db.prepare('INSERT INTO login_transactions VALUES (?,?,?,?,NULL,?,?)').bind('a'.repeat(64),'b'.repeat(64),'test-nonce','test-verifier',1,2).run();
  const result=await cleanupAuth(db,100);
  assert.equal(result.sessions,100);
  assert.equal(result.transactions,1);
  assert.equal((await db.prepare('SELECT count(*) AS n FROM sessions').first()).n,6);
  await cleanupAuth(db,100);
  assert.equal((await db.prepare('SELECT count(*) AS n FROM sessions').first()).n,1);
  assert.ok(await db.prepare('SELECT 1 FROM sessions WHERE token_digest=?').bind('f'.repeat(64)).first());
});
