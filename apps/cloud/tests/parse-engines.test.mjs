import assert from 'node:assert/strict';
import {test} from 'node:test';
import {createHash} from 'node:crypto';
import {readFile,readdir} from 'node:fs/promises';
import {runtime,migrate} from './runtime.mjs';
import {digest} from '../src/security.mjs';
import {parseRoute} from '../src/parse-jobs.mjs';
import {admitParseJob} from '../src/parse-admission.mjs';
import {processParseMessage} from '../src/parse-queue.mjs';
import {coordinateContainerRequest} from '../src/parser-container-coordinator.mjs';
import {DEFAULT_ENGINE,ENGINES,isEngine} from '../src/engines.mjs';

const token='a'.repeat(64),csrf='c'.repeat(64);
const sha=value=>createHash('sha256').update(value).digest('hex');
const pythonEngines=new URL('../../../backend/src/parserium_collector/features/analysis/engines/',import.meta.url);

async function setup(t){
 const mf=await runtime();t.after(()=>mf.dispose());
 const db=await mf.getD1Database('DB'),bucket=await mf.getR2Bucket('FILES');await migrate(db);
 const now=Math.floor(Date.now()/1000);
 await db.batch([
  db.prepare("INSERT INTO users VALUES ('u1','https://accounts.google.com','engine-tests','engines@example.test',1)"),
  db.prepare("INSERT INTO workspaces VALUES ('w1','One',1)"),
  db.prepare("INSERT INTO memberships VALUES ('w1','u1','owner')"),
  db.prepare('INSERT INTO sessions VALUES (?,?,?,?,?)').bind(sha(token),'u1',sha(csrf),now,now+3600)
 ]);
 const session={user_id:'u1',csrf_digest:await digest(csrf)};
 async function document(){
  const id=crypto.randomUUID(),bytes=new TextEncoder().encode('%PDF-engine-test');
  await db.batch([
   db.prepare("INSERT INTO upload_reservations (id,workspace_id,request_id,filename,size_bytes,reserved_bytes,status,created_at,expires_at,write_token,content_digest) VALUES (?,'w1',?,'report.pdf',?,?,'stored',1,3601,'write','digest')")
    .bind(id,crypto.randomUUID(),bytes.byteLength,bytes.byteLength+10485760),
   db.prepare("INSERT INTO documents (id,workspace_id,filename,size_bytes,created_at,validation_status,validation_page_count) VALUES (?,'w1','report.pdf',?,1,'valid',1)").bind(id,bytes.byteLength)
  ]);
  await bucket.put(`workspaces/w1/originals/${id}.pdf`,bytes);
  return id;
 }
 const submit=(documentId,{engine,requestId=crypto.randomUUID()}={})=>parseRoute(
  new Request(`https://cloud.test/api/cloud/v1/parse-jobs?document=${documentId}${engine===undefined?'':`&engine=${engine}`}`,
   {method:'POST',headers:{Origin:'https://cloud.test','X-CSRF-Token':csrf,'Idempotency-Key':requestId}}),
  {DB:db,FILES:bucket,PROCESSING_ENABLED:'true',PARSE_QUEUE:{send:async()=>{}}},session,{validatePdf:async()=>({ok:true,pageCount:1})});
 const api=path=>mf.dispatchFetch(`https://cloud.test/api/cloud/v1${path}`,{headers:{Cookie:`__Host-parserium_cloud=${token}`}});
 return {mf,db,bucket,document,submit,api};
}

function message(jobId){
 const actions=[];
 return {body:{jobId},actions,ack(){actions.push('ack');},retry(){actions.push('retry');}};
}

test('the Worker engine allowlist matches the Python registry exactly',async()=>{
 const found=[];
 for(const file of (await readdir(pythonEngines)).filter(name=>name.endsWith('_engine.py'))){
  const source=await readFile(new URL(file,pythonEngines),'utf8');
  found.push({id:/id="([a-z0-9_-]+)"/.exec(source)[1],label:/label="([^"]+)"/.exec(source)[1],ocr:/ocr=(True|False)/.exec(source)[1]==='True'});
 }
 const expected=ENGINES.map(({id,label,ocr})=>({id,label,ocr}));
 assert.deepEqual(found.sort((a,b)=>a.id.localeCompare(b.id)),expected.sort((a,b)=>a.id.localeCompare(b.id)));
 const registry=await readFile(new URL('registry.py',pythonEngines),'utf8');
 assert.equal(/DEFAULT_ENGINE = "([a-z0-9_-]+)"/.exec(registry)[1],DEFAULT_ENGINE);
});

test('engine ids are validated strictly',()=>{
 for(const id of ENGINES.map(engine=>engine.id))assert.equal(isEngine(id),true);
 for(const bad of ['','LITEPARSE','docling','liteparse ','../x',null,undefined,42,{}])assert.equal(isEngine(bad),false);
 assert.equal(Object.isFrozen(ENGINES),true);
});

test('signed-in users can list engines and anonymous callers cannot',async t=>{
 const {mf,api}=await setup(t);
 const listed=await api('/parse-engines');
 assert.equal(listed.status,200);
 const body=await listed.json();
 assert.equal(body.default,DEFAULT_ENGINE);
 assert.deepEqual(body.engines.map(engine=>engine.id),ENGINES.map(engine=>engine.id));
 assert.ok(body.engines.every(engine=>engine.label && engine.description && engine.license && typeof engine.ocr==='boolean'));
 assert.equal((await mf.dispatchFetch('https://cloud.test/api/cloud/v1/parse-engines')).status,401);
});

test('a job records the requested engine and defaults to liteparse',async t=>{
 const {db,document,submit}=await setup(t);
 const chosen=await document(),other=await document();
 const first=await submit(chosen,{engine:'markitdown'});
 assert.equal(first.status,202);assert.equal((await first.json()).job.engine,'markitdown');
 const second=await submit(other);
 assert.equal((await second.json()).job.engine,'liteparse');
 const rows=await db.prepare('SELECT document_id,engine FROM parse_jobs ORDER BY created_at,id').all();
 assert.deepEqual(Object.fromEntries(rows.results.map(row=>[row.document_id,row.engine])),{[chosen]:'markitdown',[other]:'liteparse'});
});

test('the document list and detail report the engine each job used',async t=>{
 const {document,submit,api}=await setup(t);
 const chosen=await document(),unparsed=await document();
 assert.equal((await submit(chosen,{engine:'markitdown'})).status,202);
 const listed=await (await api('/documents?workspace=w1')).json();
 const byId=Object.fromEntries(listed.documents.map(row=>[row.id,row.job_engine]));
 assert.deepEqual(byId,{[chosen]:'markitdown',[unparsed]:null});
 const detail=await (await api(`/documents/${chosen}`)).json();
 assert.equal(detail.document.job_engine,'markitdown');
});

test('unknown engines are rejected before any job is created',async t=>{
 const {db,document,submit}=await setup(t),id=await document();
 for(const engine of ['docling','LITEPARSE','..%2Fx','']){
  const response=await submit(id,{engine});
  assert.equal(response.status,400);assert.deepEqual(await response.json(),{error:'invalid_engine'});
 }
 assert.equal((await db.prepare('SELECT count(*) AS n FROM parse_jobs').first()).n,0);
});

test('replaying a request with a different engine conflicts while the same engine is idempotent',async t=>{
 const {db,document,submit}=await setup(t),id=await document(),requestId=crypto.randomUUID();
 const created=await submit(id,{engine:'markitdown',requestId});
 assert.equal(created.status,202);const {job}=await created.json();
 const same=await submit(id,{engine:'markitdown',requestId});
 assert.equal((await same.json()).job.id,job.id);
 const changed=await submit(id,{engine:'liteparse',requestId});
 assert.equal(changed.status,409);assert.deepEqual(await changed.json(),{error:'parse_conflict'});
 assert.equal((await db.prepare('SELECT engine FROM parse_jobs WHERE id=?').bind(job.id).first()).engine,'markitdown');
});

test('admission rejects unknown engines directly',async t=>{
 const {db,document}=await setup(t),documentId=await document();
 await assert.rejects(()=>admitParseJob(db,{userId:'u1',documentId,requestId:crypto.randomUUID(),engine:'docling'},86410),/invalid_engine/);
});

test('the queue consumer runs the engine saved on the job',async t=>{
 const {db,bucket,document}=await setup(t),documentId=await document();
 const admitted=await admitParseJob(db,{userId:'u1',documentId,requestId:crypto.randomUUID(),engine:'markitdown'},86410);
 await db.prepare("UPDATE parse_jobs SET status='queued' WHERE id=?").bind(admitted.id).run();
 const seen=[],delivery=message(admitted.id);
 await processParseMessage({DB:db,FILES:bucket,PARSE_QUEUE:{send:async()=>{}},PROCESSING_ENABLED:'true'},delivery,86500,
  {parsePdf:async(bytes,engine)=>{seen.push(engine);return {ok:true,engine,pageCount:1,tableCount:0,markdown:'# Parsed',runtimeMs:900};}});
 assert.deepEqual(seen,['markitdown']);assert.deepEqual(delivery.actions,['ack']);
 assert.equal((await db.prepare('SELECT status,engine FROM parse_jobs WHERE id=?').bind(admitted.id).first()).status,'succeeded');
});

test('a document with no extractable text fails permanently without retrying',async t=>{
 const {db,bucket,document}=await setup(t),documentId=await document();
 const admitted=await admitParseJob(db,{userId:'u1',documentId,requestId:crypto.randomUUID(),engine:'markitdown'},86410);
 await db.prepare("UPDATE parse_jobs SET status='queued' WHERE id=?").bind(admitted.id).run();
 const delivery=message(admitted.id);
 await processParseMessage({DB:db,FILES:bucket,PARSE_QUEUE:{send:async()=>{}},PROCESSING_ENABLED:'true'},delivery,86500,
  {parsePdf:async()=>({ok:false,error:'no_text_extracted',retryable:false,runtimeMs:900,engine:'markitdown'})});
 assert.deepEqual(delivery.actions,['ack']);
 const row=await db.prepare('SELECT status,error_code,attempt_count FROM parse_jobs WHERE id=?').bind(admitted.id).first();
 assert.deepEqual({...row},{status:'failed',error_code:'no_text_extracted',attempt_count:1});
});

function transport(response){
 const calls=[];
 return {calls,async start(){},async fetch(path,bytes,signal,engine){calls.push({path,engine});return response;},async stop(){}};
}
const pdf=new TextEncoder().encode('%PDF-1.7\n').buffer;
function jsonResponse(value){
 const body=JSON.stringify(value);
 return new Response(body,{status:200,headers:{'Content-Type':'application/json','Content-Length':String(new TextEncoder().encode(body).byteLength)}});
}
const parsed=engine=>({ok:true,engine,pageCount:1,tableCount:0,markdown:'# Parsed',runtimeMs:900});

test('the coordinator forwards the engine and returns it on success',async()=>{
 const t=transport(jsonResponse(parsed('markitdown')));
 const result=await coordinateContainerRequest('parse',pdf,t,{engine:'markitdown'});
 assert.deepEqual(t.calls,[{path:'/parse',engine:'markitdown'}]);
 assert.equal(result.ok,true);assert.equal(result.engine,'markitdown');
});

test('the coordinator rejects a result produced by a different engine than requested',async()=>{
 const t=transport(jsonResponse(parsed('liteparse')));
 const result=await coordinateContainerRequest('parse',pdf,t,{engine:'markitdown'});
 assert.deepEqual(result,{ok:false,error:'parser_unavailable',retryable:true,runtimeMs:60000});
});

test('the coordinator refuses unknown engines before starting a container',async()=>{
 let started=0;
 const t={...transport(jsonResponse(parsed('x'))),async start(){started++;}};
 await assert.rejects(()=>coordinateContainerRequest('parse',pdf,t,{engine:'docling'}),/invalid_engine/);
 assert.equal(started,0);
});

test('no_text_extracted passes through the coordinator as a permanent failure',async()=>{
 const t=transport(jsonResponse({ok:false,error:'no_text_extracted',retryable:false,runtimeMs:700,engine:'markitdown'}));
 const result=await coordinateContainerRequest('parse',pdf,t,{engine:'markitdown'});
 assert.deepEqual(result,{ok:false,error:'no_text_extracted',retryable:false,runtimeMs:700});
});
