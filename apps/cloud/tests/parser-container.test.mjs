import assert from 'node:assert/strict';
import {test} from 'node:test';
import {coordinateContainerRequest} from '../src/parser-container-coordinator.mjs';

const pdf=new TextEncoder().encode('%PDF-1.7\n').buffer;

function transport(response,{start}={}){
 const calls={start:[],fetch:[],stop:0};
 return {calls,
  async start(signal){calls.start.push(signal);if(start)await start(signal);},
  async fetch(path,bytes,signal){calls.fetch.push({path,bytes,signal});return response;},
  async stop(){calls.stop++;}
 };
}

function jsonResponse(value,headers={}){
 const body=JSON.stringify(value);
 return new Response(body,{status:200,headers:{'Content-Type':'application/json','Content-Length':String(new TextEncoder().encode(body).byteLength),...headers}});
}

test('coordinator enforces binary input bounds before starting a container',async()=>{
 const t=transport(jsonResponse({ok:true,pageCount:1}));
 await assert.rejects(()=>coordinateContainerRequest('validate',new Uint8Array(0),t),/invalid_input/);
 await assert.rejects(()=>coordinateContainerRequest('parse',new Uint8Array(10_485_761),t),/invalid_input/);
 await assert.rejects(()=>coordinateContainerRequest('other',pdf,t),/invalid_action/);
 assert.equal(t.calls.start.length,0);
});

test('valid validation response is bounded and leaves the container warm',async()=>{
 const t=transport(jsonResponse({ok:true,pageCount:1}));
 const result=await coordinateContainerRequest('validate',pdf,t);
 assert.deepEqual(result,{ok:true,pageCount:1});
 assert.equal(t.calls.start.length,1);assert.equal(t.calls.fetch.length,1);assert.equal(t.calls.stop,0);
 assert.equal(t.calls.fetch[0].path,'/validate');assert.equal(t.calls.fetch[0].bytes.byteLength,pdf.byteLength);
 assert.equal(t.calls.start[0],t.calls.fetch[0].signal);
});

test('validation failure is sanitized and stops the container',async()=>{
 const t=transport(jsonResponse({ok:false,error:'encrypted_pdf',retryable:false,detail:'do not expose'}));
 assert.deepEqual(await coordinateContainerRequest('validate',pdf,t),{ok:false,error:'encrypted_pdf',retryable:false});
 assert.equal(t.calls.stop,1);
});

test('parse returns only a valid bounded protocol result and always stops',async()=>{
 const value={ok:true,pageCount:1,tableCount:2,markdown:'| A |\n|---|',runtimeMs:19,extra:'hidden'};
 const t=transport(jsonResponse(value));
 assert.deepEqual(await coordinateContainerRequest('parse',pdf,t),{ok:true,pageCount:1,tableCount:2,markdown:'| A |\n|---|',runtimeMs:19});
 assert.equal(t.calls.stop,1);
});

test('invalid and oversized container responses fail safely without exposing details',async()=>{
 const invalid=transport(jsonResponse({ok:false,error:'secret platform detail',retryable:false}));
 assert.deepEqual(await coordinateContainerRequest('parse',pdf,invalid),{ok:false,error:'parser_unavailable',retryable:true,runtimeMs:60000});
 assert.equal(invalid.calls.stop,1);
 const oversized=transport(new Response('x',{headers:{'Content-Type':'application/json','Content-Length':'10485761'}}));
 assert.deepEqual(await coordinateContainerRequest('parse',pdf,oversized),{ok:false,error:'parser_unavailable',retryable:true,runtimeMs:60000});
 assert.equal(oversized.calls.stop,1);
});

test('one deadline covers startup and request work',async()=>{
 let fetched=false;
 const t=transport(jsonResponse({ok:true,pageCount:1}),{start:signal=>new Promise((resolve,reject)=>{
  signal.addEventListener('abort',()=>reject(signal.reason),{once:true});
 })});
 t.fetch=async()=>{fetched=true;return jsonResponse({ok:true,pageCount:1});};
 assert.deepEqual(await coordinateContainerRequest('validate',pdf,t,{timeoutMs:20}),
  {ok:false,error:'container_start_failed',retryable:true});
 assert.equal(fetched,false);assert.equal(t.calls.stop,1);
});
