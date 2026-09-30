import assert from 'node:assert/strict';
import {test} from 'node:test';
import {runtime,migrate} from './runtime.mjs';
import {admitParseJob} from '../src/parse-admission.mjs';

const DAY=86400,RESERVATION=120000;

async function setup(t){
 const mf=await runtime();t.after(()=>mf.dispose());const db=await mf.getD1Database('DB');await migrate(db);
 await db.batch([
  db.prepare("INSERT INTO users VALUES ('u1','https://accounts.google.com','parse-admission','parse@example.test',1)"),
  db.prepare("INSERT INTO workspaces VALUES ('w1','One',1),('w2','Two',1)"),
  db.prepare("INSERT INTO memberships VALUES ('w1','u1','owner'),('w2','u1','owner')")
 ]);
 async function document(workspace,id=crypto.randomUUID(),status='valid'){
  const request=crypto.randomUUID();
  await db.batch([
   db.prepare("INSERT INTO upload_reservations (id,workspace_id,request_id,filename,size_bytes,reserved_bytes,status,created_at,expires_at,write_token,content_digest) VALUES (?,?,?,?,100,10485860,'stored',1,3601,'write','digest')")
    .bind(id,workspace,request,`${id}.pdf`),
   db.prepare('INSERT INTO documents (id,workspace_id,filename,size_bytes,created_at,validation_status,validation_page_count) VALUES (?,?,?,?,1,?,1)')
    .bind(id,workspace,`${id}.pdf`,100,status)
  ]);
  return id;
 }
 return {db,document};
}

test('authorized validated document admission is workspace-idempotent',async t=>{
 const {db,document}=await setup(t),documentId=await document('w1'),requestId=crypto.randomUUID();
 const first=await admitParseJob(db,{userId:'u1',documentId,requestId},DAY+10);
 const repeated=await admitParseJob(db,{userId:'u1',documentId,requestId},DAY+11);
 assert.equal(first.created,true);assert.equal(first.status,'pending_dispatch');
 assert.equal(repeated.created,false);assert.equal(repeated.id,first.id);
 const sameDocument=await admitParseJob(db,{userId:'u1',documentId,requestId:crypto.randomUUID()},DAY+12);
 assert.equal(sameDocument.created,false);assert.equal(sameDocument.id,first.id);
 assert.equal((await db.prepare('SELECT count(*) AS n FROM parse_jobs').first()).n,1);
});

test('changed document under an idempotency key conflicts',async t=>{
 const {db,document}=await setup(t),first=await document('w1'),second=await document('w1'),requestId=crypto.randomUUID();
 await admitParseJob(db,{userId:'u1',documentId:first,requestId},DAY+10);
 await assert.rejects(()=>admitParseJob(db,{userId:'u1',documentId:second,requestId},DAY+11),/parse_conflict/);
});

test('outsiders, revoked members, and unvalidated documents are not admitted',async t=>{
 const {db,document}=await setup(t),valid=await document('w1'),invalid=await document('w2',crypto.randomUUID(),'invalid');
 await db.prepare("INSERT INTO users VALUES ('u2','https://accounts.google.com','outsider','outside@example.test',1)").run();
 await assert.rejects(()=>admitParseJob(db,{userId:'u2',documentId:valid,requestId:crypto.randomUUID()},DAY+10),/not_found/);
 await assert.rejects(()=>admitParseJob(db,{userId:'u1',documentId:invalid,requestId:crypto.randomUUID()},DAY+10),/document_not_validated/);
 await db.prepare("DELETE FROM memberships WHERE workspace_id='w1'").run();
 await assert.rejects(()=>admitParseJob(db,{userId:'u1',documentId:valid,requestId:crypto.randomUUID()},DAY+10),/not_found/);
 assert.equal((await db.prepare('SELECT count(*) AS n FROM parse_jobs').first()).n,0);
});

test('invalid admission input never writes a job',async t=>{
 const {db,document}=await setup(t),documentId=await document('w1');
 for(const input of [
  {userId:'',documentId,requestId:crypto.randomUUID()},
  {userId:'u1',documentId:'bad',requestId:crypto.randomUUID()},
  {userId:'u1',documentId,requestId:'not-a-uuid'}
 ]) await assert.rejects(()=>admitParseJob(db,input,DAY+10),/invalid_parse/);
 assert.equal((await db.prepare('SELECT count(*) AS n FROM parse_jobs').first()).n,0);
});

test('workspace daily admission stops after twenty jobs',async t=>{
 const {db,document}=await setup(t);
 for(let i=0;i<20;i++){
  const documentId=await document('w1');
  await admitParseJob(db,{userId:'u1',documentId,requestId:crypto.randomUUID()},DAY+10+i);
 }
 const rejected=await document('w1');
 await assert.rejects(()=>admitParseJob(db,{userId:'u1',documentId:rejected,requestId:crypto.randomUUID()},DAY+100),/daily_job_limit/);
 assert.equal((await db.prepare('SELECT count(*) AS n FROM parse_jobs').first()).n,20);
});

test('concurrent global reservations cannot exceed sixty runtime minutes',async t=>{
 const {db,document}=await setup(t);
 for(let i=0;i<29;i++){
  const workspace=i<15?'w1':'w2',documentId=await document(workspace);
  await admitParseJob(db,{userId:'u1',documentId,requestId:crypto.randomUUID()},DAY+10+i);
 }
 const a=await document('w2'),b=await document('w2');
 const settled=await Promise.allSettled([
  admitParseJob(db,{userId:'u1',documentId:a,requestId:crypto.randomUUID()},DAY+100),
  admitParseJob(db,{userId:'u1',documentId:b,requestId:crypto.randomUUID()},DAY+100)
 ]);
 assert.equal(settled.filter(result=>result.status==='fulfilled').length,1);
 assert.equal(settled.filter(result=>result.status==='rejected' && result.reason.message==='processing_budget_exceeded').length,1);
 const totals=await db.prepare("SELECT count(*) AS n,sum(CASE WHEN status IN ('succeeded','failed') THEN runtime_ms ELSE reserved_runtime_ms END) AS runtime FROM parse_jobs").first();
 assert.equal(totals.n,30);assert.equal(totals.runtime,30*RESERVATION);
});

test('terminal runtime reconciliation releases only unused reservation',async t=>{
 const {db,document}=await setup(t);
 for(let i=0;i<30;i++){
  const workspace=i<15?'w1':'w2',documentId=await document(workspace);
  await admitParseJob(db,{userId:'u1',documentId,requestId:crypto.randomUUID()},DAY+10+i);
 }
 const rejected=await document('w2');
 await assert.rejects(()=>admitParseJob(db,{userId:'u1',documentId:rejected,requestId:crypto.randomUUID()},DAY+100),/processing_budget_exceeded/);
 const completed=(await db.prepare('SELECT id FROM parse_jobs ORDER BY created_at,id LIMIT 2').all()).results;
 await db.prepare("UPDATE parse_jobs SET status='succeeded',runtime_ms=7000,completed_at=?,updated_at=? WHERE id=?").bind(DAY+200,DAY+200,completed[0].id).run();
 await assert.rejects(()=>admitParseJob(db,{userId:'u1',documentId:rejected,requestId:crypto.randomUUID()},DAY+200),/processing_budget_exceeded/);
 await db.prepare("UPDATE parse_jobs SET status='succeeded',runtime_ms=7000,completed_at=?,updated_at=? WHERE id=?").bind(DAY+201,DAY+201,completed[1].id).run();
 const admitted=await admitParseJob(db,{userId:'u1',documentId:rejected,requestId:crypto.randomUUID()},DAY+201);
 assert.equal(admitted.created,true);
});
