// Local integration checks only: real Miniflare D1/R2, no external API mocks or network requests.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {test} from 'node:test';
import {runtime,migrate} from './runtime.mjs';
import {digest} from '../src/security.mjs';

test('cloud expansion protects connections, search history, and DOCX originals',async t=>{
 const encryption=crypto.getRandomValues(new Uint8Array(32));
 const mf=await runtime({FIRECRAWL_CREDENTIAL_KEY:Buffer.from(encryption).toString('base64')});t.after(()=>mf.dispose());
 const db=await mf.getD1Database('DB'),bucket=await mf.getR2Bucket('FILES');await migrate(db);
 const token='c'.repeat(64),csrf='d'.repeat(64),now=Math.floor(Date.now()/1000);
 await db.batch([
  db.prepare("INSERT INTO users VALUES ('local-owner','https://accounts.google.com','local-expansion-check','check@example.test',1)"),
  db.prepare("INSERT INTO workspaces VALUES ('local-w1','Local test workspace',1),('local-w2','Other local workspace',1)"),
  db.prepare("INSERT INTO memberships VALUES ('local-w1','local-owner','owner')"),
  db.prepare('INSERT INTO sessions VALUES (?,?,?,?,?)').bind(await digest(token),'local-owner',await digest(csrf),now,now+3600)
 ]);
 const request=(path,options={})=>mf.dispatchFetch('https://cloud.test/api/cloud/v1'+path,{...options,headers:{Cookie:'__Host-parserium_cloud='+token,Origin:'https://cloud.test','X-CSRF-Token':csrf,...options.headers}});
 const connection='/discovery/connection?workspace=local-w1';
 assert.equal((await request('/discovery/connection?workspace=local-w2')).status,404);
 assert.equal((await request(connection,{method:'PUT',body:'{}',headers:{'X-CSRF-Token':''}})).status,403);
 assert.equal((await request(connection,{method:'PUT',body:'null'})).status,400);
 // A nonfunctional test credential exercises encryption only and is never sent to Firecrawl.
 const testCredential='fc-local-encryption-check-only';
 assert.equal((await request(connection,{method:'PUT',body:JSON.stringify({api_key:testCredential})})).status,200);
 const stored=await db.prepare('SELECT encrypted_key FROM firecrawl_connections WHERE workspace_id=?').bind('local-w1').first();
 assert.ok(stored.encrypted_key);assert.ok(!stored.encrypted_key.includes(testCredential));
 const summary=await (await request(connection)).json();assert.equal(summary.configured,true);assert.equal(summary.api_key,undefined);
 const [iv,body]=stored.encrypted_key.split('.').map(value=>Buffer.from(value,'base64'));
 const key=await crypto.subtle.importKey('raw',encryption,'AES-GCM',false,['decrypt']);
 assert.equal(new TextDecoder().decode(await crypto.subtle.decrypt({name:'AES-GCM',iv,additionalData:new TextEncoder().encode('local-w1')},key,body)),testCredential);
 await assert.rejects(()=>crypto.subtle.decrypt({name:'AES-GCM',iv,additionalData:new TextEncoder().encode('local-w2')},key,body));
 assert.equal((await request('/discovery/searches?workspace=local-w1',{method:'POST',body:'null'})).status,400);
 const searchId=crypto.randomUUID(),requestId=crypto.randomUUID();
 await db.prepare("INSERT INTO discovery_searches(id,workspace_id,request_id,query,file_type,result_limit,status,created_at,updated_at) VALUES (?,'local-w1',?,'Local search recovery check','pdf',5,'running',?,?)")
  .bind(searchId,requestId,now-100,now-100).run();
 const history=await (await request('/discovery/searches?workspace=local-w1')).json();
 assert.equal(history.searches[0].status,'failed');assert.equal(history.searches[0].error_code,'search_interrupted');
 const replay=await request('/discovery/searches?workspace=local-w1',{method:'POST',headers:{'Idempotency-Key':requestId},body:JSON.stringify({query:'Local search recovery check',file_type:'pdf',limit:5})});
 assert.equal(replay.status,200);assert.equal((await replay.json()).search.id,searchId);
 assert.equal((await request('/discovery/searches?workspace=local-w1',{method:'POST',headers:{'Idempotency-Key':requestId},body:JSON.stringify({query:'Local search recovery check',file_type:'pdf',limit:10})})).status,409);
 const bytes=await readFile('../../tests/fixtures/analysis/ruled-table.docx');
 const uploaded=await request('/uploads?workspace=local-w1&filename=ruled-table.docx',{method:'POST',headers:{'Content-Type':'application/vnd.openxmlformats-officedocument.wordprocessingml.document','Idempotency-Key':crypto.randomUUID()},body:bytes});
 assert.equal(uploaded.status,201);const {document}=await uploaded.json();
 assert.equal((await bucket.list()).objects[0].key,`workspaces/local-w1/originals/${document.id}.docx`);
 const downloaded=await request('/files/'+document.id);assert.equal(downloaded.status,200);assert.deepEqual(Buffer.from(await downloaded.arrayBuffer()),bytes);
 assert.equal((await request('/files/'+document.id,{method:'DELETE'})).status,204);assert.equal((await bucket.list()).objects.length,0);
 await db.prepare("UPDATE memberships SET role='member' WHERE workspace_id='local-w1'").run();
 assert.equal((await request(connection,{method:'DELETE'})).status,403);
 await db.prepare("UPDATE memberships SET role='owner' WHERE workspace_id='local-w1'").run();
 assert.equal((await request(connection,{method:'DELETE'})).status,204);assert.equal((await (await request(connection)).json()).configured,false);
});

test('search history recognizes terminal document path segments without guessing ambiguous links',async t=>{
 const mf=await runtime();t.after(()=>mf.dispose());
 const db=await mf.getD1Database('DB');await migrate(db);
 const token='e'.repeat(64),csrf='f'.repeat(64),now=Math.floor(Date.now()/1000),searchId=crypto.randomUUID();
 const results=JSON.stringify([
  {id:'result-pdf',url:'https://documents.example.test/article/123/pdf',title:'PDF endpoint',description:'',file_type:null},
  {id:'result-pdf-slash',url:'https://documents.example.test/article/456/pdf/',title:'PDF endpoint with slash',description:'',file_type:null},
  {id:'result-docx',url:'https://documents.example.test/article/789/docx',title:'DOCX endpoint',description:'',file_type:null},
  {id:'result-docx-slash',url:'https://documents.example.test/article/987/docx/',title:'DOCX endpoint with slash',description:'',file_type:null},
  {id:'result-link',url:'https://documents.example.test/article/654/download',title:'Ambiguous endpoint',description:'',file_type:null}
 ]);
 await db.batch([
  db.prepare("INSERT INTO users VALUES ('type-owner','https://accounts.google.com','type-check','type-check@example.test',1)"),
  db.prepare("INSERT INTO workspaces VALUES ('type-workspace','Type test workspace',1)"),
  db.prepare("INSERT INTO memberships VALUES ('type-workspace','type-owner','owner')"),
  db.prepare('INSERT INTO sessions VALUES (?,?,?,?,?)').bind(await digest(token),'type-owner',await digest(csrf),now,now+3600),
  db.prepare("INSERT INTO discovery_searches(id,workspace_id,request_id,query,file_type,result_limit,status,results_json,created_at,updated_at) VALUES (?,'type-workspace',?,'Document type check','all',5,'succeeded',?,?,?)")
   .bind(searchId,crypto.randomUUID(),results,now,now)
 ]);
 const response=await mf.dispatchFetch('https://cloud.test/api/cloud/v1/discovery/searches?workspace=type-workspace',{headers:{Cookie:'__Host-parserium_cloud='+token}});
 assert.equal(response.status,200);
 const history=await response.json();
 assert.deepEqual(history.searches[0].results.map(result=>result.file_type),['pdf','pdf','docx','docx',null]);
});
