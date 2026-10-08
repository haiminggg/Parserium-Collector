import {originalKey} from './upload-cleanup.mjs';

const MAX_OUTPUT=10485760,MAX_RUNTIME=60000,LEASE_SECONDS=90;
const retryableErrors=new Set(['parser_timeout','parser_unavailable','storage_unavailable','container_start_failed']);
const permanentErrors=new Set(['parser_failed','no_text_extracted','output_too_large','original_missing','invalid_pdf','invalid_docx','encrypted_pdf','page_limit_exceeded']);
const UUID=/^[a-f0-9]{8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}$/;

export const attemptOutputKey=(row,fence)=>`workspaces/${row.workspace_id}/outputs/${row.id}/${fence}.md`;

async function jobRow(db,id){
 return db.prepare(`SELECT p.*,d.size_bytes,d.filename,d.validation_page_count FROM parse_jobs p
  JOIN documents d ON d.id=p.document_id WHERE p.id=?`).bind(id).first();
}

function runtimeOf(result,fallback=MAX_RUNTIME){
 return Number.isSafeInteger(result?.runtimeMs) && result.runtimeMs>0?Math.min(MAX_RUNTIME,result.runtimeMs):fallback;
}

async function completeFailure(db,row,fence,result,now){
 const runtime=runtimeOf(result),raw=typeof result?.error==='string'?result.error:'parser_unavailable';
 const error=(retryableErrors.has(raw)||permanentErrors.has(raw))?raw:'parser_unavailable';
 const retry=result?.retryable===true && retryableErrors.has(error) && row.attempt_count<2;
 if(retry){
  const updated=await db.prepare(`UPDATE parse_jobs SET status='queued',runtime_ms=MIN(120000,runtime_ms+?),
   error_code=?,fence_token=NULL,lease_expires_at=NULL,updated_at=? WHERE id=? AND status='running' AND fence_token=?`)
   .bind(runtime,error,now,row.id,fence).run();
  return updated.meta.changes===1?'retry':'stale';
 }
 const updated=await db.prepare(`UPDATE parse_jobs SET status='failed',runtime_ms=MIN(120000,runtime_ms+?),
  error_code=?,fence_token=NULL,lease_expires_at=NULL,updated_at=?,completed_at=?
  WHERE id=? AND status='running' AND fence_token=?`).bind(runtime,error,now,now,row.id,fence).run();
 return updated.meta.changes===1?'failed':'stale';
}

export async function processParseMessage(env,message,now=Math.floor(Date.now()/1000),parser){
 if(env.PROCESSING_ENABLED!=='true') {message.retry({delaySeconds:60});return;}
 const jobId=message?.body?.jobId;
 if(typeof jobId!=='string' || !UUID.test(jobId)){message.ack();return;}
 let current=await jobRow(env.DB,jobId);
 if(!current || current.status==='succeeded' || current.status==='failed'){message.ack();return;}
 if(current.status==='running'){message.retry({delaySeconds:30});return;}
 const fence=crypto.randomUUID();
 const claimed=await env.DB.prepare(`UPDATE parse_jobs SET status='running',attempt_count=attempt_count+1,
  fence_token=?,lease_expires_at=?,error_code=NULL,updated_at=? WHERE id=? AND status IN ('pending_dispatch','queued')
  AND attempt_count<2 RETURNING *`).bind(fence,now+LEASE_SECONDS,now,jobId).first();
 if(!claimed){
  current=await jobRow(env.DB,jobId);
  if(!current || current.status==='succeeded' || current.status==='failed')message.ack();
  else message.retry({delaySeconds:30});
  return;
 }
 const row=await jobRow(env.DB,jobId);
 const original=await env.FILES?.get(originalKey({workspace_id:row.workspace_id,id:row.document_id,filename:row.filename}));
 if(!original || original.size!==row.size_bytes){
  await completeFailure(env.DB,row,fence,{error:'original_missing',retryable:false,runtimeMs:1},now);
  message.ack();return;
 }
 let result;
 try{
  if(!parser || typeof parser.parsePdf!=='function')throw Error('parser unavailable');
  result=await parser.parsePdf(await original.arrayBuffer(),row.engine);
 }catch{result={ok:false,error:'parser_unavailable',retryable:true,runtimeMs:MAX_RUNTIME};}
 if(!result || result.ok!==true){
  const disposition=await completeFailure(env.DB,row,fence,result,now);
  if(disposition==='retry')message.retry({delaySeconds:5});else message.ack();
  return;
 }
 if(typeof result.markdown!=='string' || !Number.isSafeInteger(result.pageCount) || result.pageCount<1 || result.pageCount>20 ||
    !Number.isSafeInteger(result.tableCount) || result.tableCount<0){
  await completeFailure(env.DB,row,fence,{error:'parser_failed',retryable:false,runtimeMs:runtimeOf(result)},now);
  message.ack();return;
 }
 const bytes=new TextEncoder().encode(result.markdown);
 if(bytes.byteLength>MAX_OUTPUT){
  await completeFailure(env.DB,row,fence,{error:'output_too_large',retryable:false,runtimeMs:runtimeOf(result)},now);
  message.ack();return;
 }
 const key=attemptOutputKey(row,fence);
 try{
  const stored=await env.FILES.put(key,bytes,{onlyIf:{etagDoesNotMatch:'*'},httpMetadata:{contentType:'text/markdown; charset=utf-8'}});
  const confirmed=stored && await env.FILES.head(key);
  if(!confirmed || confirmed.size!==bytes.byteLength)throw Error('storage unavailable');
 }catch{
  const disposition=await completeFailure(env.DB,row,fence,{error:'storage_unavailable',retryable:true,runtimeMs:runtimeOf(result)},now);
  if(disposition==='retry')message.retry({delaySeconds:5});else message.ack();
  return;
 }
 const attached=await env.DB.prepare(`UPDATE parse_jobs SET status='succeeded',runtime_ms=MIN(120000,runtime_ms+?),
  output_key=?,output_size_bytes=?,page_count=?,table_count=?,error_code=NULL,fence_token=NULL,lease_expires_at=NULL,
  updated_at=?,completed_at=? WHERE id=? AND status='running' AND fence_token=?`)
  .bind(runtimeOf(result),key,bytes.byteLength,result.pageCount,result.tableCount,now,now,row.id,fence).run();
 if(attached.meta.changes!==1){await env.FILES.delete(key);}
 message.ack();
}

export async function reconcileParseJobs(env,now=Math.floor(Date.now()/1000),limit=25){
 if(env.PROCESSING_ENABLED!=='true' || !env.PARSE_QUEUE || !env.FILES)return {processed:0,failed:0};
 const bounded=Number.isSafeInteger(limit)?Math.max(1,Math.min(25,limit)):25;
 const candidates=await env.DB.prepare(`SELECT id FROM parse_jobs WHERE
  (status='running' AND lease_expires_at<=?) OR status='pending_dispatch'
  ORDER BY CASE status WHEN 'running' THEN 0 ELSE 1 END,updated_at,id LIMIT ?`).bind(now,bounded).all();
 let processed=0,failed=0;
 for(const candidate of candidates.results){
  try{
   let row=await jobRow(env.DB,candidate.id);
   if(!row)continue;
   if(row.status==='running' && row.lease_expires_at<=now){
    const fence=row.fence_token;
    if(fence){
     const key=attemptOutputKey(row,fence);await env.FILES.delete(key);
     if(await env.FILES.head(key))throw Error('storage unavailable');
    }
    if(row.attempt_count>=2){
     await env.DB.prepare(`UPDATE parse_jobs SET status='failed',runtime_ms=MIN(120000,runtime_ms+60000),
      error_code='parser_timeout',fence_token=NULL,lease_expires_at=NULL,updated_at=?,completed_at=?
      WHERE id=? AND status='running' AND fence_token=?`).bind(now,now,row.id,fence).run();
     processed++;continue;
    }
    const recovered=await env.DB.prepare(`UPDATE parse_jobs SET status='pending_dispatch',runtime_ms=MIN(120000,runtime_ms+60000),
     error_code='parser_timeout',fence_token=NULL,lease_expires_at=NULL,updated_at=?
     WHERE id=? AND status='running' AND fence_token=?`).bind(now,row.id,fence).run();
    if(recovered.meta.changes!==1){processed++;continue;}
    row={...row,status:'pending_dispatch'};
   }
   if(row.status==='pending_dispatch'){
    await env.PARSE_QUEUE.send({jobId:row.id});
    await env.DB.prepare("UPDATE parse_jobs SET status='queued',updated_at=? WHERE id=? AND status='pending_dispatch'").bind(now,row.id).run();
   }
   processed++;
  }catch{failed++;}
 }
 return {processed,failed};
}
