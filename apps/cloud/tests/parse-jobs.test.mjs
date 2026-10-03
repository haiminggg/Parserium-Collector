import assert from 'node:assert/strict';
import {test} from 'node:test';
import {runtime,migrate} from './runtime.mjs';
import {digest} from '../src/security.mjs';
import {parseRoute} from '../src/parse-jobs.mjs';

const csrf='c'.repeat(64);

async function setup(t){
 const mf=await runtime();t.after(()=>mf.dispose());const db=await mf.getD1Database('DB'),bucket=await mf.getR2Bucket('FILES');await migrate(db);
 await db.batch([
  db.prepare("INSERT INTO users VALUES ('u1','https://accounts.google.com','parse-routes','parse@example.test',1),('u2','https://accounts.google.com','parse-outsider','outside@example.test',1)"),
  db.prepare("INSERT INTO workspaces VALUES ('w1','One',1),('w2','Two',1)"),
  db.prepare("INSERT INTO memberships VALUES ('w1','u1','owner'),('w2','u2','owner')")
 ]);
 const session={user_id:'u1',csrf_digest:await digest(csrf)},outsider={user_id:'u2',csrf_digest:await digest(csrf)};
 async function document(workspace='w1',bytes=new TextEncoder().encode('%PDF-real-test')){
  const id=crypto.randomUUID(),requestId=crypto.randomUUID(),filename='report.pdf';
  await db.batch([
   db.prepare("INSERT INTO upload_reservations (id,workspace_id,request_id,filename,size_bytes,reserved_bytes,status,created_at,expires_at,write_token,content_digest) VALUES (?,?,?,?,?,?, 'stored',1,3601,'write','digest')")
    .bind(id,workspace,requestId,filename,bytes.byteLength,bytes.byteLength+10485760),
   db.prepare('INSERT INTO documents (id,workspace_id,filename,size_bytes,created_at) VALUES (?,?,?,?,1)').bind(id,workspace,filename,bytes.byteLength)
  ]);
  await bucket.put(`workspaces/${workspace}/originals/${id}.pdf`,bytes);
  return {id,bytes,workspace};
 }
 function request(path,{method='GET',requestId=crypto.randomUUID(),origin='https://cloud.test'}={}){
  return new Request('https://cloud.test/api/cloud/v1'+path,{method,headers:{Origin:origin,'X-CSRF-Token':csrf,'Idempotency-Key':requestId}});
 }
 function environment(send=async()=>{}){return {DB:db,FILES:bucket,PROCESSING_ENABLED:'true',PARSE_QUEUE:{send}};}
 return {db,bucket,session,outsider,document,request,environment};
}

test('submission validates the stored original before durable admission and publishes only a job id',async t=>{
 const {db,session,document,request,environment}=await setup(t),source=await document(),messages=[];
 const response=await parseRoute(request(`/parse-jobs?document=${source.id}`,{method:'POST'}),environment(async body=>messages.push(body)),session,
  {validatePdf:async bytes=>({ok:bytes.byteLength===source.bytes.byteLength,pageCount:1})});
 assert.equal(response.status,202);const {job}=await response.json();assert.equal(job.status,'queued');
 assert.deepEqual(messages,[{jobId:job.id}]);assert.deepEqual(Object.keys(messages[0]),['jobId']);
 const stored=await db.prepare('SELECT validation_status,validation_page_count FROM documents WHERE id=?').bind(source.id).first();
 assert.deepEqual(stored,{validation_status:'valid',validation_page_count:1});
 assert.equal((await db.prepare('SELECT count(*) AS n FROM parse_jobs').first()).n,1);
});

test('repeated submission returns one job and does not publish twice',async t=>{
 const {db,session,document,request,environment}=await setup(t),source=await document(),messages=[],requestId=crypto.randomUUID();
 const parser={validatePdf:async()=>({ok:true,pageCount:1})},env=environment(async body=>messages.push(body));
 const first=await parseRoute(request(`/parse-jobs?document=${source.id}`,{method:'POST',requestId}),env,session,parser);
 const second=await parseRoute(request(`/parse-jobs?document=${source.id}`,{method:'POST',requestId}),env,session,parser);
 assert.equal((await second.json()).job.id,(await first.json()).job.id);
 assert.equal(messages.length,1);assert.equal((await db.prepare('SELECT count(*) AS n FROM parse_jobs').first()).n,1);
});

test('permanent validation failures are cached and never create jobs',async t=>{
 const {db,session,document,request,environment}=await setup(t);
 for(const error of ['invalid_pdf','encrypted_pdf','page_limit_exceeded']){
  const source=await document(),parser={validatePdf:async()=>({ok:false,error,retryable:false})};
  const response=await parseRoute(request(`/parse-jobs?document=${source.id}`,{method:'POST'}),environment(),session,parser);
  assert.equal(response.status,422);assert.deepEqual(await response.json(),{error});
  const stored=await db.prepare('SELECT validation_status,validation_error_code FROM documents WHERE id=?').bind(source.id).first();
  assert.deepEqual(stored,{validation_status:'invalid',validation_error_code:error});
 }
 assert.equal((await db.prepare('SELECT count(*) AS n FROM parse_jobs').first()).n,0);
});

test('submission fails closed for disabled processing, CSRF, membership, and missing originals',async t=>{
 const {db,bucket,session,document,request,environment}=await setup(t),source=await document();
 const parser={validatePdf:async()=>({ok:true,pageCount:1})};
 assert.equal((await parseRoute(request(`/parse-jobs?document=${source.id}`,{method:'POST'}),{...environment(),PROCESSING_ENABLED:'false'},session,parser)).status,503);
 assert.equal((await parseRoute(request(`/parse-jobs?document=${source.id}`,{method:'POST',origin:'https://evil.test'}),environment(),session,parser)).status,403);
 const other=await document('w2');
 assert.equal((await parseRoute(request(`/parse-jobs?document=${other.id}`,{method:'POST'}),environment(),session,parser)).status,404);
 await bucket.delete(`workspaces/w1/originals/${source.id}.pdf`);
 assert.equal((await parseRoute(request(`/parse-jobs?document=${source.id}`,{method:'POST'}),environment(),session,parser)).status,503);
 assert.equal((await db.prepare('SELECT count(*) AS n FROM parse_jobs').first()).n,0);
});

test('lost Queue publish leaves durable dispatch intent for reconciliation',async t=>{
 const {db,session,document,request,environment}=await setup(t),source=await document();
 const response=await parseRoute(request(`/parse-jobs?document=${source.id}`,{method:'POST'}),environment(async()=>{throw Error('test queue unavailable');}),session,
  {validatePdf:async()=>({ok:true,pageCount:1})});
 assert.equal(response.status,202);const {job}=await response.json();assert.equal(job.status,'pending_dispatch');
 assert.equal((await db.prepare('SELECT status FROM parse_jobs WHERE id=?').bind(job.id).first()).status,'pending_dispatch');
});

test('job status and Markdown output are workspace-authorized and durable',async t=>{
 const {db,bucket,session,outsider,document,request,environment}=await setup(t),source=await document(),env=environment();
 const submitted=await parseRoute(request(`/parse-jobs?document=${source.id}`,{method:'POST'}),env,session,{validatePdf:async()=>({ok:true,pageCount:1})});
 const job=(await submitted.json()).job,outputKey=`workspaces/w1/outputs/${job.id}/winner.md`,markdown='# Parsed\n\n| A | B |';
 await bucket.put(outputKey,markdown,{httpMetadata:{contentType:'text/markdown; charset=utf-8'}});
 const completedAt=job.created_at+1;
 await db.prepare("UPDATE parse_jobs SET status='succeeded',attempt_count=1,runtime_ms=7000,output_key=?,output_size_bytes=?,page_count=1,table_count=1,updated_at=?,completed_at=? WHERE id=?")
  .bind(outputKey,new TextEncoder().encode(markdown).byteLength,completedAt,completedAt,job.id).run();
 const status=await parseRoute(request(`/parse-jobs/${job.id}`),env,session);
 assert.equal(status.status,200);assert.deepEqual((await status.json()).job,{id:job.id,document_id:source.id,engine:'liteparse',status:'succeeded',attempt_count:1,page_count:1,table_count:1,error_code:null,output_available:true,created_at:job.created_at,updated_at:completedAt,completed_at:completedAt});
 const output=await parseRoute(request(`/parse-jobs/${job.id}/output`),env,session);
 assert.equal(output.status,200);assert.equal(await output.text(),markdown);assert.match(output.headers.get('Content-Type'),/^text\/markdown/);assert.equal(output.headers.get('Cache-Control'),'no-store');
 assert.equal((await parseRoute(request(`/parse-jobs/${job.id}`),env,outsider)).status,404);
 assert.equal((await parseRoute(request(`/parse-jobs/${job.id}/output`),env,outsider)).status,404);
});
