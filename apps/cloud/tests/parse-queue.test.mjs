import assert from 'node:assert/strict';
import {test} from 'node:test';
import {runtime,migrate} from './runtime.mjs';
import {admitParseJob} from '../src/parse-admission.mjs';
import {processParseMessage,reconcileParseJobs,attemptOutputKey} from '../src/parse-queue.mjs';

async function setup(t){
 const mf=await runtime();t.after(()=>mf.dispose());const db=await mf.getD1Database('DB'),bucket=await mf.getR2Bucket('FILES');await migrate(db);
 await db.batch([
  db.prepare("INSERT INTO users VALUES ('u','https://accounts.google.com','queue-tests','queue@example.test',1)"),
  db.prepare("INSERT INTO workspaces VALUES ('w','Test',1)"),
  db.prepare("INSERT INTO memberships VALUES ('w','u','owner')")
 ]);
 async function job({original=true,status='queued'}={}){
  const documentId=crypto.randomUUID(),uploadRequest=crypto.randomUUID(),bytes=new TextEncoder().encode('%PDF-queue-test');
  await db.batch([
   db.prepare("INSERT INTO upload_reservations (id,workspace_id,request_id,filename,size_bytes,reserved_bytes,status,created_at,expires_at,write_token,content_digest) VALUES (?,'w',?,'test.pdf',?,?,'stored',1,3601,'write','digest')")
    .bind(documentId,uploadRequest,bytes.byteLength,bytes.byteLength+10485760),
   db.prepare("INSERT INTO documents (id,workspace_id,filename,size_bytes,created_at,validation_status,validation_page_count) VALUES (?,'w','test.pdf',?,1,'valid',1)").bind(documentId,bytes.byteLength)
  ]);
  if(original)await bucket.put(`workspaces/w/originals/${documentId}.pdf`,bytes);
  const admitted=await admitParseJob(db,{userId:'u',documentId,requestId:crypto.randomUUID()},86410);
  await db.prepare('UPDATE parse_jobs SET status=? WHERE id=?').bind(status,admitted.id).run();
  return {id:admitted.id,documentId,bytes};
 }
 const env=(send=async()=>{})=>({DB:db,FILES:bucket,PARSE_QUEUE:{send},PROCESSING_ENABLED:'true'});
 return {db,bucket,job,env};
}

function message(jobId){
 const actions=[];
 return {body:{jobId},actions,ack(){actions.push({type:'ack'});},retry(options){actions.push({type:'retry',options});}};
}

const success={ok:true,pageCount:1,tableCount:1,markdown:'| A | B |\n|---|---|',runtimeMs:7000};

test('successful delivery stores attempt output and attaches only the winning fence',async t=>{
 const {db,bucket,job,env}=await setup(t),record=await job(),delivery=message(record.id);
 await processParseMessage(env(),delivery,86500,{parsePdf:async bytes=>bytes.byteLength===record.bytes.byteLength?success:null});
 assert.deepEqual(delivery.actions,[{type:'ack'}]);
 const stored=await db.prepare('SELECT * FROM parse_jobs WHERE id=?').bind(record.id).first();
 assert.equal(stored.status,'succeeded');assert.equal(stored.attempt_count,1);assert.equal(stored.runtime_ms,7000);
 assert.equal(stored.page_count,1);assert.equal(stored.table_count,1);assert.equal(stored.completed_at,86500);
 assert.equal(await (await bucket.get(stored.output_key)).text(),success.markdown);
});

test('terminal duplicate is acknowledged without creating another attempt or object',async t=>{
 const {db,bucket,job,env}=await setup(t),record=await job(),first=message(record.id);
 await processParseMessage(env(),first,86500,{parsePdf:async()=>success});
 const duplicate=message(record.id);
 await processParseMessage(env(),duplicate,86501,{parsePdf:async()=>{throw Error('must not execute');}});
 assert.deepEqual(duplicate.actions,[{type:'ack'}]);
 assert.equal((await db.prepare('SELECT attempt_count FROM parse_jobs WHERE id=?').bind(record.id).first()).attempt_count,1);
 assert.equal((await bucket.list({prefix:`workspaces/w/outputs/${record.id}/`})).objects.length,1);
});

test('active lease duplicate is delayed without consuming an attempt',async t=>{
 const {db,job,env}=await setup(t),record=await job();
 await db.prepare("UPDATE parse_jobs SET status='running',attempt_count=1,fence_token='active',lease_expires_at=90000 WHERE id=?").bind(record.id).run();
 const delivery=message(record.id);await processParseMessage(env(),delivery,86500,{parsePdf:async()=>success});
 assert.deepEqual(delivery.actions,[{type:'retry',options:{delaySeconds:30}}]);
 assert.equal((await db.prepare('SELECT attempt_count FROM parse_jobs WHERE id=?').bind(record.id).first()).attempt_count,1);
});

test('retryable failures consume at most two application attempts',async t=>{
 const {db,job,env}=await setup(t),record=await job(),failure={ok:false,error:'parser_timeout',retryable:true,runtimeMs:60000};
 const first=message(record.id);await processParseMessage(env(),first,86500,{parsePdf:async()=>failure});
 assert.deepEqual(first.actions,[{type:'retry',options:{delaySeconds:5}}]);
 let stored=await db.prepare('SELECT status,attempt_count,runtime_ms FROM parse_jobs WHERE id=?').bind(record.id).first();
 assert.deepEqual(stored,{status:'queued',attempt_count:1,runtime_ms:60000});
 const second=message(record.id);await processParseMessage(env(),second,86600,{parsePdf:async()=>failure});
 assert.deepEqual(second.actions,[{type:'ack'}]);
 stored=await db.prepare('SELECT status,attempt_count,runtime_ms,error_code FROM parse_jobs WHERE id=?').bind(record.id).first();
 assert.deepEqual(stored,{status:'failed',attempt_count:2,runtime_ms:120000,error_code:'parser_timeout'});
});

test('permanent parser failure and missing original terminate without retry',async t=>{
 const {db,job,env}=await setup(t),invalid=await job(),invalidMessage=message(invalid.id);
 await processParseMessage(env(),invalidMessage,86500,{parsePdf:async()=>({ok:false,error:'parser_failed',retryable:false,runtimeMs:3000})});
 assert.deepEqual(invalidMessage.actions,[{type:'ack'}]);
 assert.deepEqual(await db.prepare('SELECT status,error_code FROM parse_jobs WHERE id=?').bind(invalid.id).first(),{status:'failed',error_code:'parser_failed'});
 const missing=await job({original:false}),missingMessage=message(missing.id);
 await processParseMessage(env(),missingMessage,86500,{parsePdf:async()=>success});
 assert.deepEqual(missingMessage.actions,[{type:'ack'}]);
 assert.deepEqual(await db.prepare('SELECT status,error_code FROM parse_jobs WHERE id=?').bind(missing.id).first(),{status:'failed',error_code:'original_missing'});
});

test('oversized and stale successful output is never attached',async t=>{
 const {db,bucket,job,env}=await setup(t),oversized=await job(),largeMessage=message(oversized.id);
 await processParseMessage(env(),largeMessage,86500,{parsePdf:async()=>({...success,markdown:'x'.repeat(10485761)})});
 assert.deepEqual(await db.prepare('SELECT status,error_code FROM parse_jobs WHERE id=?').bind(oversized.id).first(),{status:'failed',error_code:'output_too_large'});
 assert.deepEqual(largeMessage.actions,[{type:'ack'}]);
 const stale=await job(),staleMessage=message(stale.id);
 await processParseMessage(env(),staleMessage,86500,{parsePdf:async()=>{
  await db.prepare("UPDATE parse_jobs SET status='queued',fence_token=NULL,lease_expires_at=NULL WHERE id=?").bind(stale.id).run();
  return success;
 }});
 assert.deepEqual(staleMessage.actions,[{type:'ack'}]);
 assert.equal((await bucket.list({prefix:`workspaces/w/outputs/${stale.id}/`})).objects.length,0);
});

test('reconciliation is bounded and recovers expired leases conservatively',async t=>{
 const {db,bucket,job,env}=await setup(t),expired=await job(),pending=await job({status:'pending_dispatch'}),messages=[];
 await db.prepare("UPDATE parse_jobs SET status='running',attempt_count=1,fence_token='expired-fence',lease_expires_at=86499 WHERE id=?").bind(expired.id).run();
 await bucket.put(attemptOutputKey({workspace_id:'w',id:expired.id},'expired-fence'),'orphan');
 const first=await reconcileParseJobs(env(async body=>messages.push(body)),86500,1);
 assert.deepEqual(first,{processed:1,failed:0});
 const recovered=await db.prepare('SELECT status,runtime_ms,fence_token FROM parse_jobs WHERE id=?').bind(expired.id).first();
 assert.deepEqual(recovered,{status:'queued',runtime_ms:60000,fence_token:null});
 assert.equal(await bucket.head(attemptOutputKey({workspace_id:'w',id:expired.id},'expired-fence')),null);
 assert.deepEqual(messages,[{jobId:expired.id}]);
 const second=await reconcileParseJobs(env(async body=>messages.push(body)),86501,1);
 assert.deepEqual(second,{processed:1,failed:0});assert.deepEqual(messages,[{jobId:expired.id},{jobId:pending.id}]);
});

test('disabled processing retries without claiming a job',async t=>{
 const {db,job,env}=await setup(t),record=await job(),delivery=message(record.id);
 await processParseMessage({...env(),PROCESSING_ENABLED:'false'},delivery,86500,{parsePdf:async()=>success});
 assert.deepEqual(delivery.actions,[{type:'retry',options:{delaySeconds:60}}]);
 assert.equal((await db.prepare('SELECT attempt_count FROM parse_jobs WHERE id=?').bind(record.id).first()).attempt_count,0);
});
