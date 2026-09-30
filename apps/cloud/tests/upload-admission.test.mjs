import assert from 'node:assert/strict';
import {test} from 'node:test';
import {runtime,migrate} from './runtime.mjs';
import * as admission from '../src/upload-admission.mjs';

async function setup(t){
 const mf=await runtime();t.after(()=>mf.dispose());
 const db=await mf.getD1Database('DB');await migrate(db);
 await db.batch([
  db.prepare("INSERT INTO users VALUES ('u1','https://accounts.google.com','test-upload-subject','upload@example.test',1)"),
  db.prepare("INSERT INTO workspaces VALUES ('w1','Local test one',1),('w2','Local test two',1)"),
  db.prepare("INSERT INTO memberships VALUES ('w1','u1','owner')")
 ]);
 return db;
}
const input={userId:'u1',workspaceId:'w1',requestId:'01234567-89ab-4cde-8fab-0123456789ab',filename:'report.pdf',sizeBytes:100};
test('upload admission reserves input and bounded output for the authorized workspace',async t=>{
 assert.equal(typeof admission.reserveUpload,'function');
 const db=await setup(t);const r=await admission.reserveUpload(db,input,100);
 assert.equal(r.status,'reserved');assert.equal(r.expiresAt,3700);
 const row=await db.prepare('SELECT * FROM upload_reservations').first();
 assert.equal(row.reserved_bytes,10485760+100);assert.equal(row.workspace_id,'w1');
 assert.equal(row.filename,'report.pdf');
});
test('duplicate upload is idempotent but changed contents metadata conflicts',async t=>{
 assert.equal(typeof admission.reserveUpload,'function');
 const db=await setup(t);
 const first=await admission.reserveUpload(db,input,100);
 const second=await admission.reserveUpload(db,input,101);
 assert.equal(first.id,second.id);assert.equal(second.expiresAt,first.expiresAt);
 await assert.rejects(()=>admission.reserveUpload(db,{...input,sizeBytes:101},101),/conflict/);
 assert.equal((await db.prepare('SELECT count(*) AS n FROM upload_reservations').first()).n,1);
});
test('upload admission denies outsiders and revoked members including idempotent replay',async t=>{
 assert.equal(typeof admission.reserveUpload,'function');
 const db=await setup(t);
 await assert.rejects(()=>admission.reserveUpload(db,{...input,workspaceId:'w2'},100),/not_found/);
 await admission.reserveUpload(db,input,100);
 await db.prepare('DELETE FROM memberships').run();
 await assert.rejects(()=>admission.reserveUpload(db,input,101),/not_found/);
});
test('invalid upload metadata never reserves storage',async t=>{
 assert.equal(typeof admission.reserveUpload,'function');
 const db=await setup(t);
 for(const change of [{sizeBytes:0},{sizeBytes:10485761},{sizeBytes:1.5},{filename:'../file.pdf'},{filename:'report.exe'},{filename:'x\r\n.pdf'},{requestId:'bad'}]){
  await assert.rejects(()=>admission.reserveUpload(db,{...input,...change},100),/invalid_upload/);
 }
 assert.equal((await db.prepare('SELECT count(*) AS n FROM upload_reservations').first()).n,0);
});
test('simultaneous retries reserve once and expired request IDs cannot reopen uploads',async t=>{
 const db=await setup(t);
 const results=await Promise.all(Array.from({length:5},()=>admission.reserveUpload(db,input,100)));
 assert.equal(new Set(results.map(r=>r.id)).size,1);
 assert.equal((await db.prepare('SELECT count(*) AS n FROM upload_reservations').first()).n,1);
 await assert.rejects(()=>admission.reserveUpload(db,input,3700),/upload_conflict/);
 assert.equal((await db.prepare('SELECT count(*) AS n FROM upload_reservations').first()).n,1);
});
test('concurrent reservations cannot exceed workspace quota; expired reservations still count until cleanup',async t=>{
 assert.equal(typeof admission.reserveUpload,'function');
 const db=await setup(t);
 // Real local quota records, each obeying the per-upload cap. No mocked database.
 for(let i=0;i<50;i++) await admission.reserveUpload(db,{...input,requestId:crypto.randomUUID(),sizeBytes:10485760},100);
 const outcomes=await Promise.allSettled(Array.from({length:5},()=>admission.reserveUpload(db,{...input,requestId:crypto.randomUUID(),sizeBytes:10485760},4000)));
 assert.equal(outcomes.filter(x=>x.status==='fulfilled').length,1);
 assert.equal(outcomes.filter(x=>x.status==='rejected' && x.reason.message==='storage_quota_exceeded').length,4);
 const row=await db.prepare('SELECT sum(reserved_bytes) AS bytes FROM upload_reservations').first();
 assert.ok(row.bytes<=1073741824);
 await assert.rejects(()=>admission.reserveUpload(db,input,4000),/storage_quota_exceeded/);
});
