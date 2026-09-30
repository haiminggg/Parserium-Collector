import {reserveUpload} from './upload-admission.mjs';
import {digest} from './security.mjs';
import {originalKey as key,finishDeletion} from './upload-cleanup.mjs';
const MAX=10485760;
const headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer'};
const json=(body,status)=>Response.json(body,{status,headers});
async function lookup(db,id,userId){
 return db.prepare(`SELECT r.* FROM upload_reservations r JOIN memberships m ON m.workspace_id=r.workspace_id
  WHERE r.id=? AND m.user_id=? AND r.status IN ('stored','deleting')`).bind(id,userId).first();
}
async function readPDF(request){
 const filename=new URL(request.url).searchParams.get('filename')||'';
 const docx=filename.toLowerCase().endsWith('.docx');
 const expected=docx?'application/vnd.openxmlformats-officedocument.wordprocessingml.document':'application/pdf';
 if(request.headers.get('Content-Type')?.split(';')[0].trim()!==expected) throw Error(docx?'invalid_docx':'invalid_pdf');
 const declared=request.headers.get('Content-Length');
 if(declared && (!/^\d+$/.test(declared) || Number(declared)>MAX)) throw Error('upload_too_large');
 if(!request.body) throw Error('invalid_pdf');
 const reader=request.body.getReader(),buffer=new Uint8Array(MAX),deadline=Date.now()+30000;
 let size=0;
 try {
  while(true){
   let timer;
   const timeout=new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('upload_timeout')),Math.max(1,deadline-Date.now()));});
   let chunk;
   try {chunk=await Promise.race([reader.read(),timeout]);}finally{clearTimeout(timer);}
   if(chunk.done) break;
   if(size+chunk.value.byteLength>MAX) throw Error('upload_too_large');
   buffer.set(chunk.value,size);size+=chunk.value.byteLength;
  }
 }catch(error){void reader.cancel().catch(()=>{});throw error;}finally{reader.releaseLock();}
 if(size<5 || (docx?!(buffer[0]===80 && buffer[1]===75 && buffer[2]===3 && buffer[3]===4):new TextDecoder().decode(buffer.subarray(0,5))!=='%PDF-')) throw Error(docx?'invalid_docx':'invalid_pdf');
 return buffer.slice(0,size);
}
async function upload(request,env,session){
 const url=new URL(request.url),workspaceId=url.searchParams.get('workspace'),filename=url.searchParams.get('filename');
 if(!workspaceId || workspaceId.length>128 || !await env.DB.prepare('SELECT 1 FROM memberships WHERE workspace_id=? AND user_id=?').bind(workspaceId,session.user_id).first()) return json({error:'not_found'},404);
 const bytes=await readPDF(request);
 const hash=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),v=>v.toString(16).padStart(2,'0')).join('');
 const requestId=request.headers.get('Idempotency-Key');
 const previous=await env.DB.prepare(`SELECT r.* FROM upload_reservations r JOIN memberships m ON m.workspace_id=r.workspace_id
  WHERE r.workspace_id=? AND r.request_id=? AND m.user_id=?`).bind(workspaceId,requestId,session.user_id).first();
 if(previous?.status==='stored'){
  if(previous.filename!==filename || previous.content_digest!==hash) throw Error('upload_conflict');
  return json({document:{id:previous.id,filename,validation:'awaiting_validation'}},200);
 }
 const now=Math.floor(Date.now()/1000);
 const reserved=await reserveUpload(env.DB,{userId:session.user_id,workspaceId,requestId,filename,sizeBytes:bytes.byteLength},now);
 const writeToken=crypto.randomUUID();
 const claimed=await env.DB.prepare(`UPDATE upload_reservations SET write_token=?,content_digest=? WHERE id=? AND status='reserved'
  AND write_token IS NULL AND expires_at>? AND EXISTS(SELECT 1 FROM memberships WHERE workspace_id=? AND user_id=?)`)
  .bind(writeToken,hash,reserved.id,now,workspaceId,session.user_id).run();
 if(claimed.meta.changes!==1) throw Error('upload_conflict');
 // Failed/ambiguous writes retain their reservation for reconciliation, never release quota speculatively.
 const objectKey=key({workspace_id:workspaceId,id:reserved.id,filename});
 const guard=await env.FILES.put(objectKey,writeToken,{onlyIf:{etagDoesNotMatch:'*'}});
 if(!guard) throw Error('upload_conflict');
 const stored=await env.FILES.put(objectKey,bytes,{onlyIf:{etagMatches:guard.etag},httpMetadata:{contentType:'application/octet-stream'}});
 if(!stored || stored.size!==bytes.byteLength) throw Error('storage_unavailable');
 const confirmed=await env.FILES.head(objectKey);
 if(!confirmed || confirmed.size!==bytes.byteLength) throw Error('storage_unavailable');
 const result=await env.DB.batch([
  env.DB.prepare(`INSERT INTO documents (id,workspace_id,filename,size_bytes,created_at)
   SELECT id,workspace_id,filename,size_bytes,? FROM upload_reservations WHERE id=? AND write_token=? AND status='reserved'
   AND EXISTS(SELECT 1 FROM memberships WHERE workspace_id=? AND user_id=?)`).bind(now,reserved.id,writeToken,workspaceId,session.user_id),
  env.DB.prepare(`UPDATE upload_reservations SET status='stored' WHERE id=? AND write_token=?
   AND EXISTS(SELECT 1 FROM documents WHERE id=?)`).bind(reserved.id,writeToken,reserved.id)
 ]);
 if(result[0].meta.changes!==1 || result[1].meta.changes!==1) throw Error('storage_unavailable');
 return json({document:{id:reserved.id,filename,validation:'awaiting_validation'}},201);
}
export async function fileRoute(request,env,session){
 const url=new URL(request.url);
 if(!['GET','POST','DELETE'].includes(request.method)) return json({error:'method_not_allowed'},405);
 if(request.method!=='GET'){
  const csrf=request.headers.get('X-CSRF-Token');
  if(request.headers.get('Origin')!==url.origin || !csrf || !/^[a-f0-9]{64}$/.test(csrf) || await digest(csrf)!==session.csrf_digest) return json({error:'forbidden'},403);
 }
 if(!env.FILES) return json({error:'storage_unavailable'},503);
 try {
  if(url.pathname==='/api/cloud/v1/uploads' && request.method==='POST') return await upload(request,env,session);
  const id=url.pathname.slice('/api/cloud/v1/files/'.length);
  if(!/^[a-f0-9-]{36}$/.test(id)) return json({error:'not_found'},404);
  const row=await lookup(env.DB,id,session.user_id);
  if(!row) return json({error:'not_found'},404);
  if(request.method==='GET'){
   if(row.status!=='stored') return json({error:'not_found'},404);
   const object=await env.FILES.get(key(row));
   if(!object || (await lookup(env.DB,id,session.user_id))?.status!=='stored') return json({error:'not_found'},404);
   return new Response(object.body,{headers:{...headers,'Content-Type':'application/octet-stream',
    'Content-Disposition':`attachment; filename="document.${row.filename.toLowerCase().endsWith('.docx')?'docx':'pdf'}"; filename*=UTF-8''${encodeURIComponent(row.filename).replace(/['()*]/g,c=>'%'+c.charCodeAt(0).toString(16))}`,
    'Content-Length':String(object.size),'Content-Security-Policy':"sandbox; default-src 'none'"}});
  }
  if(request.method==='DELETE'){
   const claimed=await env.DB.prepare(`UPDATE upload_reservations SET status='deleting' WHERE id=? AND status IN ('stored','deleting')
    AND EXISTS(SELECT 1 FROM memberships WHERE workspace_id=? AND user_id=?)
    AND NOT EXISTS(SELECT 1 FROM parse_jobs WHERE document_id=? AND status IN ('pending_dispatch','queued','running'))`)
    .bind(id,row.workspace_id,session.user_id,id).run();
   if(claimed.meta.changes!==1){
    const active=await env.DB.prepare("SELECT 1 FROM parse_jobs WHERE document_id=? AND status IN ('pending_dispatch','queued','running')")
     .bind(id).first();
    return active?json({error:'parse_in_progress'},409):json({error:'not_found'},404);
   }
   await finishDeletion(env.DB,env.FILES,row);
   return new Response(null,{status:204,headers});
  }
  return json({error:'method_not_allowed'},405);
 }catch(error){
  const statuses={invalid_upload:400,invalid_pdf:415,invalid_docx:415,upload_too_large:413,upload_timeout:408,not_found:404,upload_conflict:409,storage_quota_exceeded:409,parse_in_progress:409};
  return json({error:statuses[error.message]?error.message:'storage_unavailable'},statuses[error.message]||503);
 }
}
