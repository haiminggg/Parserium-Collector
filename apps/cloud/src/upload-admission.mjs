const MAX_INPUT=10485760,MAX_OUTPUT=10485760,WORKSPACE_QUOTA=1073741824;
const uuid=/^[a-f0-9]{8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}$/i;
// Internal admission only. A reservation is not a validated document or a parsing job.
// Never release quota merely on expiry: storage cleanup must confirm object removal.
export async function reserveUpload(db,input,now=Math.floor(Date.now()/1000)) {
 const {userId,workspaceId,requestId,filename,sizeBytes}=input;
 if(typeof userId!=='string' || !userId || userId.length>128 ||
    typeof workspaceId!=='string' || !workspaceId || workspaceId.length>128 ||
    typeof requestId!=='string' || !uuid.test(requestId) ||
    typeof filename!=='string' || filename.length>255 || !filename.trim() || filename!==filename.trim() ||
    /[\x00-\x1f\x7f/\\]/.test(filename) || !/\.(pdf|docx)$/i.test(filename) ||
    !Number.isSafeInteger(sizeBytes) || sizeBytes<1 || sizeBytes>MAX_INPUT ||
    !Number.isSafeInteger(now) || now<0 || !Number.isSafeInteger(now+3600)) throw Error('invalid_upload');
 const reserved=sizeBytes+MAX_OUTPUT;
 await db.prepare(`INSERT INTO upload_reservations
   (id,workspace_id,request_id,filename,size_bytes,reserved_bytes,status,created_at,expires_at)
   SELECT ?,?,?,?,?,?,'reserved',?,? WHERE
   EXISTS (SELECT 1 FROM memberships WHERE workspace_id=? AND user_id=?)
   AND COALESCE((SELECT sum(reserved_bytes) FROM upload_reservations WHERE workspace_id=?),0)+?<=?
   ON CONFLICT(workspace_id,request_id) DO NOTHING`)
  .bind(crypto.randomUUID(),workspaceId,requestId,filename,sizeBytes,reserved,now,now+3600,
    workspaceId,userId,workspaceId,reserved,WORKSPACE_QUOTA).run();
 const row=await db.prepare(`SELECT r.id,r.filename,r.size_bytes,r.status,r.expires_at
   FROM upload_reservations r JOIN memberships m ON m.workspace_id=r.workspace_id
   WHERE r.workspace_id=? AND r.request_id=? AND m.user_id=?`)
  .bind(workspaceId,requestId,userId).first();
 if(row){
  if(row.filename!==filename || row.size_bytes!==sizeBytes) throw Error('upload_conflict');
  if(row.status!=='reserved' || row.expires_at<=now) throw Error('upload_conflict');
  return {id:row.id,status:row.status,expiresAt:row.expires_at};
 }
 if(!await db.prepare('SELECT 1 FROM memberships WHERE workspace_id=? AND user_id=?').bind(workspaceId,userId).first()) throw Error('not_found');
 throw Error('storage_quota_exceeded');
}
