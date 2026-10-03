import {admitParseJob} from './parse-admission.mjs';
import {isEngine} from './engines.mjs';
import {digest} from './security.mjs';
import {originalKey} from './upload-cleanup.mjs';

const PREFIX='/api/cloud/v1/parse-jobs',MAX_INPUT=10485760;
const UUID=/^[a-f0-9]{8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}$/;
const headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer'};
const json=(body,status=200)=>Response.json(body,{status,headers});
const validationErrors=new Set(['invalid_pdf','encrypted_pdf','page_limit_exceeded']);

function parserFor(env){
 if(!env.PARSER?.getByName)return null;
 const stub=env.PARSER.getByName('global-parser');
 return stub && typeof stub.validatePdf==='function'?{validatePdf:bytes=>stub.validatePdf(bytes)}:null;
}

async function authorizedDocument(db,id,userId){
 return db.prepare(`SELECT d.*,r.status AS storage_status FROM documents d
  JOIN upload_reservations r ON r.id=d.id AND r.status='stored'
  JOIN memberships m ON m.workspace_id=d.workspace_id
  WHERE d.id=? AND m.user_id=?`).bind(id,userId).first();
}

async function authorizedJob(db,id,userId){
 return db.prepare(`SELECT p.*,d.filename FROM parse_jobs p JOIN documents d ON d.id=p.document_id
  JOIN memberships m ON m.workspace_id=p.workspace_id WHERE p.id=? AND m.user_id=?`)
  .bind(id,userId).first();
}

function jobBody(row){
 return {id:row.id,document_id:row.document_id,engine:row.engine,status:row.status,attempt_count:row.attempt_count,
  page_count:row.page_count,table_count:row.table_count,error_code:row.error_code,
  output_available:row.status==='succeeded' && typeof row.output_key==='string',created_at:row.created_at,
  updated_at:row.updated_at,completed_at:row.completed_at};
}

async function submit(request,env,session,parser){
 if(env.PROCESSING_ENABLED!=='true')return json({error:'processing_disabled'},503);
 if(!env.FILES || !env.PARSE_QUEUE || !parser || typeof parser.validatePdf!=='function')return json({error:'processing_unavailable'},503);
 const url=new URL(request.url),documentId=url.searchParams.get('document'),requestId=request.headers.get('Idempotency-Key');
 if(!requestId || !UUID.test(requestId))return json({error:'invalid_parse'},400);
 if(!documentId || !UUID.test(documentId))return json({error:'not_found'},404);
 const requestedEngine=url.searchParams.get('engine');
 if(requestedEngine!==null && !isEngine(requestedEngine))return json({error:'invalid_engine'},400);
 const document=await authorizedDocument(env.DB,documentId,session.user_id);
 if(!document)return json({error:'not_found'},404);
 if(document.validation_status==='invalid')return json({error:document.format_error_code||document.validation_error_code||'invalid_pdf'},422);
 if(document.validation_status!=='valid'){
  const object=await env.FILES.get(originalKey(document));
  if(!object || object.size!==document.size_bytes || object.size>MAX_INPUT)return json({error:'storage_unavailable'},503);
  let result;
  try{result=await parser.validatePdf(await object.arrayBuffer());}catch{return json({error:'processing_unavailable'},503);}
  if(!result || typeof result!=='object')return json({error:'processing_unavailable'},503);
  if(result.ok!==true){
   if(result.error==='invalid_docx'){
    await env.DB.prepare(`UPDATE documents SET validation_status='invalid',format_error_code='invalid_docx',validation_page_count=NULL
     WHERE id=? AND validation_status='awaiting_validation' AND EXISTS(SELECT 1 FROM memberships WHERE workspace_id=? AND user_id=?)`)
     .bind(documentId,document.workspace_id,session.user_id).run();
    return json({error:'invalid_docx'},422);
   }
   if(!validationErrors.has(result.error))return json({error:'processing_unavailable'},503);
   await env.DB.prepare(`UPDATE documents SET validation_status='invalid',validation_error_code=?,validation_page_count=NULL
    WHERE id=? AND validation_status='awaiting_validation' AND EXISTS(SELECT 1 FROM memberships WHERE workspace_id=? AND user_id=?)`)
    .bind(result.error,documentId,document.workspace_id,session.user_id).run();
   return json({error:result.error},422);
  }
  if(!Number.isSafeInteger(result.pageCount) || result.pageCount<1 || result.pageCount>20)return json({error:'processing_unavailable'},503);
  await env.DB.prepare(`UPDATE documents SET validation_status='valid',validation_page_count=?,validation_error_code=NULL
   WHERE id=? AND validation_status='awaiting_validation' AND EXISTS(SELECT 1 FROM memberships WHERE workspace_id=? AND user_id=?)`)
   .bind(result.pageCount,documentId,document.workspace_id,session.user_id).run();
 }
 const now=Math.floor(Date.now()/1000);
 let admitted;
 try{admitted=await admitParseJob(env.DB,{userId:session.user_id,documentId,requestId,engine:requestedEngine??undefined},now);}
 catch(error){
  const statuses={invalid_parse:400,invalid_engine:400,not_found:404,document_not_validated:409,parse_conflict:409,daily_job_limit:429,processing_budget_exceeded:429};
  return json({error:statuses[error.message]?error.message:'processing_unavailable'},statuses[error.message]||503);
 }
 if(admitted.created){
  try{
   await env.PARSE_QUEUE.send({jobId:admitted.id});
   await env.DB.prepare("UPDATE parse_jobs SET status='queued',updated_at=? WHERE id=? AND status='pending_dispatch'")
    .bind(now,admitted.id).run();
  }catch{/* The persisted pending_dispatch row is reconciled by the scheduled handler. */}
 }
 const row=await authorizedJob(env.DB,admitted.id,session.user_id);
 return json({job:jobBody(row)},202);
}

async function output(env,session,id){
 const row=await authorizedJob(env.DB,id,session.user_id);
 if(!row || row.status!=='succeeded' || !row.output_key)return json({error:'not_found'},404);
 const object=await env.FILES?.get(row.output_key);
 if(!object || object.size!==row.output_size_bytes)return json({error:'not_found'},404);
 const repeated=await authorizedJob(env.DB,id,session.user_id);
 if(!repeated || repeated.status!=='succeeded' || repeated.output_key!==row.output_key)return json({error:'not_found'},404);
 const name=row.filename.replace(/\.(pdf|docx)$/i,'')+'.md';
 return new Response(object.body,{headers:{...headers,'Content-Type':'text/markdown; charset=utf-8',
  'Content-Disposition':`attachment; filename="parsed.md"; filename*=UTF-8''${encodeURIComponent(name).replace(/['()*]/g,c=>'%'+c.charCodeAt(0).toString(16))}`,
  'Content-Length':String(object.size),'Content-Security-Policy':"sandbox; default-src 'none'"}});
}

export async function parseRoute(request,env,session,parser=parserFor(env)){
 const url=new URL(request.url);
 if(url.pathname===PREFIX && request.method==='POST'){
  const csrf=request.headers.get('X-CSRF-Token');
  if(request.headers.get('Origin')!==url.origin || !csrf || !/^[a-f0-9]{64}$/.test(csrf) || await digest(csrf)!==session.csrf_digest)
   return json({error:'forbidden'},403);
  return submit(request,env,session,parser);
 }
 if(request.method!=='GET' || !url.pathname.startsWith(PREFIX+'/'))return json({error:'not_found'},404);
 const tail=url.pathname.slice(PREFIX.length+1),isOutput=tail.endsWith('/output'),id=isOutput?tail.slice(0,-7):tail;
 if(!UUID.test(id) || id.includes('/'))return json({error:'not_found'},404);
 if(isOutput)return output(env,session,id);
 const row=await authorizedJob(env.DB,id,session.user_id);
 return row?json({job:jobBody(row)}):json({error:'not_found'},404);
}
