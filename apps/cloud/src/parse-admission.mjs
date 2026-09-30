const UUID=/^[a-f0-9]{8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}$/;
const DAILY_WORKSPACE_JOBS=20,DAILY_GLOBAL_RUNTIME_MS=3600000,JOB_RESERVATION_MS=120000;

function validId(value){return typeof value==='string' && value.length<=128 && UUID.test(value);}

export async function admitParseJob(db,input,now=Math.floor(Date.now()/1000)){
 const {userId,documentId,requestId}=input;
 if(typeof userId!=='string' || !userId || userId.length>128 || !validId(documentId) || !validId(requestId) ||
    !Number.isSafeInteger(now) || now<0) throw Error('invalid_parse');
 const dayStart=now-(now%86400),id=crypto.randomUUID();
 const inserted=await db.prepare(`INSERT INTO parse_jobs
  (id,workspace_id,document_id,request_id,status,attempt_count,day_start,reserved_runtime_ms,runtime_ms,created_at,updated_at)
  SELECT ?,d.workspace_id,d.id,?,'pending_dispatch',0,?,?,0,?,? FROM documents d
  JOIN upload_reservations r ON r.id=d.id AND r.status='stored'
  JOIN memberships m ON m.workspace_id=d.workspace_id
  WHERE d.id=? AND m.user_id=? AND d.validation_status='valid'
  AND (SELECT count(*) FROM parse_jobs p WHERE p.workspace_id=d.workspace_id AND p.day_start=?)<?
  AND COALESCE((SELECT sum(CASE WHEN p.status IN ('succeeded','failed') THEN p.runtime_ms ELSE p.reserved_runtime_ms END)
    FROM parse_jobs p WHERE p.day_start=?),0)+?<=?
  ON CONFLICT DO NOTHING`)
  .bind(id,requestId,dayStart,JOB_RESERVATION_MS,now,now,documentId,userId,dayStart,DAILY_WORKSPACE_JOBS,
    dayStart,JOB_RESERVATION_MS,DAILY_GLOBAL_RUNTIME_MS).run();
 const byRequest=await db.prepare(`SELECT p.id,p.status,p.document_id FROM parse_jobs p
  JOIN documents d ON d.id=? AND d.workspace_id=p.workspace_id
  JOIN memberships m ON m.workspace_id=p.workspace_id
  WHERE p.request_id=? AND m.user_id=?`).bind(documentId,requestId,userId).first();
 if(byRequest){
  if(byRequest.document_id!==documentId) throw Error('parse_conflict');
  return {id:byRequest.id,status:byRequest.status,documentId,created:inserted.meta.changes===1};
 }
 const byDocument=await db.prepare(`SELECT p.id,p.status FROM parse_jobs p JOIN memberships m ON m.workspace_id=p.workspace_id
  WHERE p.document_id=? AND m.user_id=?`).bind(documentId,userId).first();
 if(byDocument)return {id:byDocument.id,status:byDocument.status,documentId,created:false};
 const target=await db.prepare(`SELECT d.workspace_id,d.validation_status FROM documents d
  JOIN upload_reservations r ON r.id=d.id AND r.status='stored'
  JOIN memberships m ON m.workspace_id=d.workspace_id WHERE d.id=? AND m.user_id=?`).bind(documentId,userId).first();
 if(!target)throw Error('not_found');
 if(target.validation_status!=='valid')throw Error('document_not_validated');
 const workspaceUsage=await db.prepare('SELECT count(*) AS n FROM parse_jobs WHERE workspace_id=? AND day_start=?')
  .bind(target.workspace_id,dayStart).first();
 if(workspaceUsage.n>=DAILY_WORKSPACE_JOBS)throw Error('daily_job_limit');
 const globalUsage=await db.prepare(`SELECT COALESCE(sum(CASE WHEN status IN ('succeeded','failed') THEN runtime_ms ELSE reserved_runtime_ms END),0) AS n
  FROM parse_jobs WHERE day_start=?`).bind(dayStart).first();
 if(globalUsage.n+JOB_RESERVATION_MS>DAILY_GLOBAL_RUNTIME_MS)throw Error('processing_budget_exceeded');
 throw Error('parse_conflict');
}
