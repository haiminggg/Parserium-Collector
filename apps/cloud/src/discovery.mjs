import {digest} from './security.mjs';
import {fileRoute} from './files.mjs';

const PREFIX='/api/cloud/v1/discovery';
const headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'};
const json=(body,status=200)=>Response.json(body,{status,headers});
const uuid=/^[a-f0-9]{8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}$/;
const encode=bytes=>btoa(String.fromCharCode(...bytes));
const decode=text=>Uint8Array.from(atob(text),c=>c.charCodeAt(0));

async function encryptionKey(env){
 const bytes=decode(env.FIRECRAWL_CREDENTIAL_KEY||'');
 if(bytes.length!==32)throw Error('connection_storage_unavailable');
 return crypto.subtle.importKey('raw',bytes,'AES-GCM',false,['encrypt','decrypt']);
}
async function seal(env,workspace,key){
 const iv=crypto.getRandomValues(new Uint8Array(12));
 const encrypted=await crypto.subtle.encrypt({name:'AES-GCM',iv,additionalData:new TextEncoder().encode(workspace)},await encryptionKey(env),new TextEncoder().encode(key));
 return encode(iv)+'.'+encode(new Uint8Array(encrypted));
}
async function unseal(env,workspace,value){
 const [iv,encrypted]=value.split('.');
 return new TextDecoder().decode(await crypto.subtle.decrypt({name:'AES-GCM',iv:decode(iv),additionalData:new TextEncoder().encode(workspace)},await encryptionKey(env),decode(encrypted)));
}
async function boundedBody(response,max){
 if(Number(response.headers.get('Content-Length')||0)>max)throw Error('response_too_large');
 const reader=response.body?.getReader();if(!reader)throw Error('invalid_response');
 const chunks=[];let size=0;
 let timer;
 const deadline=new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('request_timeout')),45000);});
 try{while(true){const {done,value}=await Promise.race([reader.read(),deadline]);if(done)break;size+=value.byteLength;if(size>max)throw Error('response_too_large');chunks.push(value);}}
 catch(error){await reader.cancel().catch(()=>{});throw error;}
 finally{clearTimeout(timer);reader.releaseLock();}
 const bytes=new Uint8Array(size);let offset=0;for(const chunk of chunks){bytes.set(chunk,offset);offset+=chunk.byteLength;}return bytes;
}
function publicUrl(value){
 try{
  const url=new URL(value),host=url.hostname.toLowerCase().replace(/\.$/,'');
  if(url.protocol!=='https:' || url.username || url.password || (url.port && url.port!=='443') || url.href.length>2048 ||
   !host.includes('.') || !/^[a-z0-9.-]+$/.test(host) || /^[\d.]+$/.test(host) ||
   /(^|\.)(localhost|local|internal|test|invalid|example|onion)$/.test(host))return null;
  return url;
 }catch{return null;}
}
function documentType(value){
 try{
  const url=typeof value==='string'?new URL(value):value;
  const pathname=url.pathname.replace(/\/+$/,'');
  const segment=pathname.slice(pathname.lastIndexOf('/')+1).toLowerCase();
  return segment==='pdf'||segment.endsWith('.pdf')?'pdf':segment==='docx'||segment.endsWith('.docx')?'docx':null;
 }catch{return null;}
}
const record=row=>({id:row.id,query:row.query,file_type:row.file_type,result_limit:row.result_limit,status:row.status,error_code:row.error_code,created_at:row.created_at,results:row.results_json?JSON.parse(row.results_json).map(result=>({...result,file_type:result.file_type||documentType(result.url)})):[]});

async function search(request,env,session,workspace){
 const input=JSON.parse(new TextDecoder().decode(await boundedBody(request,4096)));
 if(!input || typeof input!=='object' || Array.isArray(input))return json({error:'invalid_search'},400);
 const query=typeof input.query==='string'?input.query.trim():'';
 const type=input.file_type||'all',limit=input.limit??10,requestId=request.headers.get('Idempotency-Key');
 if(!query || query.length>450 || !['pdf','docx','all'].includes(type) || !Number.isInteger(limit) || limit<1 || limit>20 || !uuid.test(requestId||''))return json({error:'invalid_search'},400);
 const prior=await env.DB.prepare('SELECT * FROM discovery_searches WHERE workspace_id=? AND request_id=?').bind(workspace,requestId).first();
 if(prior)return prior.query===query && prior.file_type===type && prior.result_limit===limit?json({search:record(prior)}):json({error:'search_conflict'},409);
 const connection=await env.DB.prepare('SELECT encrypted_key FROM firecrawl_connections WHERE workspace_id=?').bind(workspace).first();
 if(!connection)return json({error:'firecrawl_not_connected'},409);
 const apiKey=await unseal(env,workspace,connection.encrypted_key);
 const now=Math.floor(Date.now()/1000),id=crypto.randomUUID(),day=now-now%86400;
 const admitted=await env.DB.prepare(`INSERT INTO discovery_searches(id,workspace_id,request_id,query,file_type,result_limit,status,created_at,updated_at)
  SELECT ?,?,?,?,?,?,'running',?,? WHERE EXISTS(SELECT 1 FROM memberships WHERE workspace_id=? AND user_id=?)
  AND (SELECT count(*) FROM discovery_searches WHERE workspace_id=? AND created_at>=?)<20
  AND NOT EXISTS(SELECT 1 FROM discovery_searches WHERE workspace_id=? AND status='running' AND updated_at>?) ON CONFLICT DO NOTHING`)
  .bind(id,workspace,requestId,query,type,limit,now,now,workspace,session.user_id,workspace,day,workspace,now-90).run();
 if(admitted.meta.changes!==1)return json({error:'search_limit_or_busy'},429);
 try{
  // Workerd rejects the "error" redirect mode. Manual mode plus the non-2xx check refuses redirects without forwarding the key.
  const response=await fetch('https://api.firecrawl.dev/v2/search',{method:'POST',redirect:'manual',signal:AbortSignal.timeout(45000),
   headers:{Authorization:`Bearer ${apiKey}`,'Content-Type':'application/json'},
   body:JSON.stringify({query:`${query} ${type==='all'?'(filetype:pdf OR filetype:docx)':`filetype:${type}`}`,limit,sources:['web'],timeout:40000})});
  if(!response.ok){await response.body?.cancel();throw Error(response.status===401||response.status===403?'firecrawl_auth_failed':response.status===402?'firecrawl_credits_exhausted':response.status===429?'firecrawl_rate_limited':'firecrawl_unavailable');}
  const data=JSON.parse(new TextDecoder().decode(await boundedBody(response,1048576)));
  if(data.success!==true || !Array.isArray(data.data?.web))throw Error('firecrawl_unavailable');
  const seen=new Set();const results=[];
  for(const item of data.data.web.slice(0,limit)){
   if(!item || typeof item.url!=='string')continue;
   const url=publicUrl(item.url);if(!url || seen.has(url.href))continue;seen.add(url.href);
   results.push({id:crypto.randomUUID(),url:url.href,title:String(item.title||url.hostname).slice(0,500),description:String(item.description||'').slice(0,1500),file_type:documentType(url)});
  }
  await env.DB.prepare("UPDATE discovery_searches SET status='succeeded',results_json=?,updated_at=? WHERE id=? AND status='running'")
   .bind(JSON.stringify(results),Math.floor(Date.now()/1000),id).run();
 }catch(error){
  const allowed=new Set(['firecrawl_auth_failed','firecrawl_credits_exhausted','firecrawl_rate_limited','firecrawl_unavailable']);
  await env.DB.prepare("UPDATE discovery_searches SET status='failed',error_code=?,updated_at=? WHERE id=? AND status='running'")
   .bind(allowed.has(error.message)?error.message:'firecrawl_unavailable',Math.floor(Date.now()/1000),id).run();
 }
 const row=await env.DB.prepare(`SELECT s.* FROM discovery_searches s JOIN memberships m ON m.workspace_id=s.workspace_id WHERE s.id=? AND m.user_id=?`).bind(id,session.user_id).first();
 return row?json({search:record(row)}):json({error:'not_found'},404);
}

// Normal Workers fetch has no VPC bindings. Check public DNS and every redirect as additional defense.
async function checkPublicDns(url,signal){
 let addresses=0;
 for(const type of ['A','AAAA']){
  const response=await fetch('https://cloudflare-dns.com/dns-query?'+new URLSearchParams({name:url.hostname,type}),{headers:{Accept:'application/dns-json'},redirect:'manual',signal});
  if(!response.ok){await response.body?.cancel();throw Error('source_unavailable');}
  const data=JSON.parse(new TextDecoder().decode(await boundedBody(response,65536)));
  if(data.Status!==0)throw Error('source_unavailable');
  for(const answer of data.Answer||[]){
   if(answer.type===1){
    addresses++;
    const parts=answer.data.split('.').map(Number),[a,b]=parts;
    if(parts.length!==4 || parts.some(n=>!Number.isInteger(n)||n<0||n>255) || a===0||a===10||a===127||a>=224||
     (a===100&&b>=64&&b<=127)||(a===169&&b===254)||(a===172&&b>=16&&b<=31)||(a===192&&(b===168||b===0))||
     (a===198&&(b===18||b===19||b===51))||(a===203&&b===0))throw Error('source_not_public');
   }
   if(answer.type===28){
    addresses++;
    if(!/^[23][0-9a-f]{3}:/i.test(answer.data) || /^(2001:(0*:|db8:|10:)|2002:|3fff:)/i.test(answer.data))throw Error('source_not_public');
   }
  }
 }
 if(!addresses)throw Error('source_unavailable');
}
async function collect(request,env,session,workspace){
 const input=JSON.parse(new TextDecoder().decode(await boundedBody(request,1024)));
 if(!input || typeof input!=='object' || Array.isArray(input))return json({error:'invalid_collection'},400);
 if(!uuid.test(input.search_id||'') || !uuid.test(input.result_id||'') || !uuid.test(request.headers.get('Idempotency-Key')||''))return json({error:'invalid_collection'},400);
 const search=await env.DB.prepare("SELECT results_json FROM discovery_searches WHERE id=? AND workspace_id=? AND status='succeeded'").bind(input.search_id,workspace).first();
 const source=search && JSON.parse(search.results_json).find(item=>item.id===input.result_id);
 if(!source)return json({error:'not_found'},404);
 if(env.AUTH_LIMITER && !(await env.AUTH_LIMITER.limit({key:'collect:'+workspace})).success)return json({error:'collection_rate_limited'},429);
 const signal=AbortSignal.timeout(30000);let url=publicUrl(source.url),response;
 try{
  for(let redirects=0;redirects<=4;redirects++){
   if(!url || url.hostname===new URL(env.APP_ORIGIN).hostname)throw Error('source_not_public');
   await checkPublicDns(url,signal);
   response=await fetch(url,{redirect:'manual',signal,headers:{Accept:'application/pdf, application/vnd.openxmlformats-officedocument.wordprocessingml.document'}});
   if([301,302,303,307,308].includes(response.status)){
    const location=response.headers.get('Location');await response.body?.cancel();
    url=location?publicUrl(new URL(location,url).href):null;response=null;continue;
   }
   break;
  }
  if(!response?.ok)throw Error('source_unavailable');
  const bytes=await boundedBody(response,10485760),pdf=new TextDecoder().decode(bytes.slice(0,5))==='%PDF-';
  const docx=bytes[0]===80&&bytes[1]===75&&bytes[2]===3&&bytes[3]===4;
  if(!pdf&&!docx)return json({error:'source_not_document'},422);
  const extension=pdf?'pdf':'docx';
  const base=(decodeURIComponent(url.pathname.split('/').pop()||'document').replace(/\.(pdf|docx)$/i,'').replace(/[\x00-\x1f\x7f/\\]/g,'_').trim()||'document').slice(0,240);
  const uploadUrl=new URL('/api/cloud/v1/uploads',request.url);uploadUrl.search=new URLSearchParams({workspace,filename:`${base}.${extension}`}).toString();
  const uploadHeaders=new Headers(request.headers);uploadHeaders.set('Content-Type',pdf?'application/pdf':'application/vnd.openxmlformats-officedocument.wordprocessingml.document');uploadHeaders.set('Content-Length',String(bytes.byteLength));
  const uploaded=await fileRoute(new Request(uploadUrl,{method:'POST',headers:uploadHeaders,body:bytes}),env,session);
  if(uploaded.ok){const data=await uploaded.clone().json();await env.DB.prepare('UPDATE documents SET source_url=? WHERE id=? AND workspace_id=?').bind(source.url,data.document.id,workspace).run();}
  return uploaded;
 }catch(error){return json({error:['source_not_public','response_too_large'].includes(error.message)?error.message:'source_unavailable'},422);}
}

export async function discoveryRoute(request,env,session){
 const url=new URL(request.url),workspace=url.searchParams.get('workspace');
 const membership=workspace && workspace.length<=128?await env.DB.prepare('SELECT role FROM memberships WHERE workspace_id=? AND user_id=?').bind(workspace,session.user_id).first():null;
 if(!membership)return json({error:'not_found'},404);
 if(request.method!=='GET'){
  const csrf=request.headers.get('X-CSRF-Token');
  if(request.headers.get('Origin')!==url.origin || !csrf || !/^[a-f0-9]{64}$/.test(csrf) || await digest(csrf)!==session.csrf_digest)return json({error:'forbidden'},403);
 }
 try{
  if(url.pathname===PREFIX+'/connection'){
   if(request.method==='GET'){
    const row=await env.DB.prepare('SELECT updated_at FROM firecrawl_connections WHERE workspace_id=?').bind(workspace).first();
    return json({configured:!!row,updated_at:row?.updated_at??null,can_manage:membership.role==='owner',storage_available:!!env.FIRECRAWL_CREDENTIAL_KEY});
   }
   if(membership.role!=='owner')return json({error:'owner_required'},403);
   if(request.method==='PUT'){
    const input=JSON.parse(new TextDecoder().decode(await boundedBody(request,2048)));
    if(!input || typeof input!=='object' || Array.isArray(input))return json({error:'invalid_api_key'},400);
    if(typeof input.api_key!=='string'||!/^fc-[A-Za-z0-9_-]{10,200}$/.test(input.api_key))return json({error:'invalid_api_key'},400);
    const encrypted=await seal(env,workspace,input.api_key);
    const saved=await env.DB.prepare(`INSERT INTO firecrawl_connections SELECT ?,?,? WHERE EXISTS(SELECT 1 FROM memberships WHERE workspace_id=? AND user_id=? AND role='owner')
     ON CONFLICT(workspace_id) DO UPDATE SET encrypted_key=excluded.encrypted_key,updated_at=excluded.updated_at`)
     .bind(workspace,encrypted,Math.floor(Date.now()/1000),workspace,session.user_id).run();return saved.meta.changes===1?json({configured:true}):json({error:'not_found'},404);
   }
   if(request.method==='DELETE'){await env.DB.prepare("DELETE FROM firecrawl_connections WHERE workspace_id=? AND EXISTS(SELECT 1 FROM memberships WHERE workspace_id=? AND user_id=? AND role='owner')").bind(workspace,workspace,session.user_id).run();return new Response(null,{status:204,headers});}
  }
  if(url.pathname===PREFIX+'/searches'){
   if(request.method==='POST')return await search(request,env,session,workspace);
   if(request.method==='GET'){
    await env.DB.prepare("UPDATE discovery_searches SET status='failed',error_code='search_interrupted',updated_at=? WHERE workspace_id=? AND status='running' AND updated_at<?")
     .bind(Math.floor(Date.now()/1000),workspace,Math.floor(Date.now()/1000)-90).run();
    const rows=await env.DB.prepare('SELECT s.* FROM discovery_searches s JOIN memberships m ON m.workspace_id=s.workspace_id WHERE s.workspace_id=? AND m.user_id=? ORDER BY s.created_at DESC,s.id DESC LIMIT 20').bind(workspace,session.user_id).all();
    return json({searches:rows.results.map(record)});
   }
  }
  if(url.pathname===PREFIX+'/collect' && request.method==='POST')return await collect(request,env,session,workspace);
  return json({error:'not_found'},404);
 }catch(error){
  if(error instanceof SyntaxError)return json({error:'invalid_request'},400);
  if(error.message==='response_too_large')return json({error:'request_too_large'},413);
  if(error.message==='request_timeout')return json({error:'request_timeout'},408);
  return json({error:'connection_storage_unavailable'},503);
 }
}
