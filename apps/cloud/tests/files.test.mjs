import assert from 'node:assert/strict';
import {test} from 'node:test';
import {readFile} from 'node:fs/promises';
import {runtime,migrate} from './runtime.mjs';
import {digest} from '../src/security.mjs';
import {admitParseJob} from '../src/parse-admission.mjs';
const pdf=await readFile('../../tests/fixtures/analysis/ruled-table.pdf');
const token='a'.repeat(64),csrf='b'.repeat(64);
async function setup(t){
 const mf=await runtime();t.after(()=>mf.dispose());const db=await mf.getD1Database('DB');await migrate(db);
 const now=Math.floor(Date.now()/1000);
 await db.batch([
  db.prepare("INSERT INTO users VALUES ('u1','https://accounts.google.com','test-files','files@example.test',1)"),
  db.prepare("INSERT INTO workspaces VALUES ('w1','Test one',1),('w2','Test two',1)"),
  db.prepare("INSERT INTO memberships VALUES ('w1','u1','owner')"),
  db.prepare('INSERT INTO sessions VALUES (?,?,?,?,?)').bind(await digest(token),'u1',await digest(csrf),now,now+3600)
 ]);
 const request=(path,options={})=>mf.dispatchFetch('https://cloud.test/api/cloud/v1'+path,{...options,headers:{Cookie:'__Host-parserium_cloud='+token,Origin:'https://cloud.test','X-CSRF-Token':csrf,...options.headers}});
 const upload=(workspace='w1',body=pdf,extra={})=>request('/uploads?workspace='+workspace+'&filename=report.pdf',{method:'POST',body,headers:{'Content-Type':'application/pdf','Idempotency-Key':crypto.randomUUID(),...extra}});
 return {mf,db,request,upload,bucket:await mf.getR2Bucket('FILES')};
}
test('private PDF upload stores original bytes and supports authenticated download and deletion',async t=>{
 const {upload,request,bucket,db}=await setup(t);const r=await upload();assert.equal(r.status,201);
 const {document}=await r.json();assert.equal(document.validation,'awaiting_validation');
 const objects=await bucket.list();assert.equal(objects.objects.length,1);
 const download=await request('/files/'+document.id);assert.equal(download.status,200);
 assert.match(download.headers.get('Content-Disposition'),/^attachment/);
 assert.equal(download.headers.get('Cache-Control'),'no-store');
 assert.deepEqual(Buffer.from(await download.arrayBuffer()),pdf);
 assert.equal((await request('/files/'+document.id,{method:'DELETE'})).status,204);
 assert.equal((await request('/files/'+document.id)).status,404);
 assert.equal((await bucket.list()).objects.length,0);
 assert.equal((await db.prepare('SELECT count(*) AS n FROM upload_reservations').first()).n,0);
});
test('file APIs enforce CSRF and workspace membership',async t=>{
 const {upload,request,db}=await setup(t);
 assert.equal((await upload('w1',pdf,{'X-CSRF-Token':''})).status,403);
 assert.equal((await upload('w2')).status,404);
 const r=await upload();assert.equal(r.status,201);const {document}=await r.json();
 assert.equal((await request('/files/'+document.id,{method:'DELETE',headers:{Origin:'https://evil.test'}})).status,403);
 await db.prepare('DELETE FROM memberships').run();
 assert.equal((await request('/files/'+document.id)).status,404);
 assert.equal((await request('/files/'+document.id,{method:'DELETE'})).status,404);
});
test('non-PDF and oversized bodies do not reserve quota or store objects',async t=>{
 const {upload,db,bucket}=await setup(t);
 assert.equal((await upload('w1',new TextEncoder().encode('not a PDF'))).status,415);
 assert.equal((await upload('w1',new Uint8Array(10485761))).status,413);
 assert.equal((await bucket.list()).objects.length,0);
 assert.equal((await db.prepare('SELECT count(*) AS n FROM upload_reservations').first()).n,0);
});
test('completed upload retries return the same document and changed bytes conflict',async t=>{
 const {upload,bucket,db}=await setup(t);const id=crypto.randomUUID();
 const a=await upload('w1',pdf,{'Idempotency-Key':id});assert.equal(a.status,201);
 const first=await a.json();
 const b=await upload('w1',pdf,{'Idempotency-Key':id});assert.equal(b.status,200);
 assert.equal((await b.json()).document.id,first.document.id);
 const changed=Buffer.from(pdf);changed[changed.length-1]^=1;
 assert.equal((await upload('w1',changed,{'Idempotency-Key':id})).status,409);
 assert.equal((await bucket.list()).objects.length,1);
 assert.equal((await db.prepare('SELECT count(*) AS n FROM documents').first()).n,1);
});
test('interrupted deletion hides the original and can be resumed without leaking quota',async t=>{
 const {upload,request,bucket,db}=await setup(t);const r=await upload();const {document}=await r.json();
 // Persist the actual intermediate state representing a stopped deletion, no storage mocks.
 await db.prepare("UPDATE upload_reservations SET status='deleting' WHERE id=?").bind(document.id).run();
 assert.equal((await request('/files/'+document.id)).status,404);
 assert.ok((await db.prepare('SELECT sum(reserved_bytes) AS n FROM upload_reservations').first()).n>0);
 assert.equal((await request('/files/'+document.id,{method:'DELETE'})).status,204);
 assert.equal((await bucket.list()).objects.length,0);
 assert.equal((await db.prepare('SELECT count(*) AS n FROM upload_reservations').first()).n,0);
});
test('missing original is not served as a successful download and its quota is retained',async t=>{
 const {upload,request,bucket,db}=await setup(t);const r=await upload();const {document}=await r.json();
 const object=(await bucket.list()).objects[0];await bucket.delete(object.key);
 assert.equal((await request('/files/'+document.id)).status,404);
 assert.equal((await db.prepare('SELECT count(*) AS n FROM upload_reservations').first()).n,1);
});
test('deletion blocks active parsing and removes confirmed Markdown with the document',async t=>{
 const {upload,request,bucket,db}=await setup(t);const r=await upload();const {document}=await r.json();
 await db.prepare("UPDATE documents SET validation_status='valid',validation_page_count=1 WHERE id=?").bind(document.id).run();
 const job=await admitParseJob(db,{userId:'u1',documentId:document.id,requestId:crypto.randomUUID()});
 await db.prepare("UPDATE parse_jobs SET status='queued' WHERE id=?").bind(job.id).run();
 const blocked=await request('/files/'+document.id,{method:'DELETE'});
 assert.equal(blocked.status,409);assert.deepEqual(await blocked.json(),{error:'parse_in_progress'});
 assert.ok(await bucket.head(`workspaces/w1/originals/${document.id}.pdf`));
 const outputKey=`workspaces/w1/outputs/${job.id}/winner.md`,markdown='| Result |';
 await bucket.put(outputKey,markdown);
 const now=Math.floor(Date.now()/1000);
 await db.prepare("UPDATE parse_jobs SET status='succeeded',attempt_count=1,runtime_ms=1000,output_key=?,output_size_bytes=?,page_count=1,table_count=1,updated_at=?,completed_at=? WHERE id=?")
  .bind(outputKey,new TextEncoder().encode(markdown).byteLength,now,now,job.id).run();
 assert.equal((await request('/files/'+document.id,{method:'DELETE'})).status,204);
 assert.equal(await bucket.head(outputKey),null);assert.equal(await bucket.head(`workspaces/w1/originals/${document.id}.pdf`),null);
 assert.equal((await db.prepare('SELECT count(*) AS n FROM parse_jobs WHERE id=?').bind(job.id).first()).n,0);
});
