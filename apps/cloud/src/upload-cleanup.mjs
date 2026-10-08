export const originalKey=row=>`workspaces/${row.workspace_id}/originals/${row.id}.${row.filename?.toLowerCase().endsWith('.docx')?'docx':'pdf'}`;
export async function finishDeletion(db,bucket,row){
 const document=await db.prepare('SELECT 1 FROM documents WHERE id=?').bind(row.id).first();
 const job=document?await db.prepare('SELECT status,output_key FROM parse_jobs WHERE document_id=?').bind(row.id).first():null;
 if(job && ['pending_dispatch','queued','running'].includes(job.status)) throw Error('parse_in_progress');
 if(job?.output_key){
  const expectedPrefix=`workspaces/${row.workspace_id}/outputs/`;
  if(!job.output_key.startsWith(expectedPrefix)) throw Error('storage_unavailable');
  await bucket.delete(job.output_key);
  if(await bucket.head(job.output_key)) throw Error('storage_unavailable');
 }
 if(!document && row.write_token){
  // Keep this zero-byte fence permanently. Removing it could admit a delayed guard creation.
  await bucket.put(originalKey(row),new Uint8Array(0),{customMetadata:{kind:'abandoned-upload'}});
  const fence=await bucket.head(originalKey(row));
  if(!fence || fence.size!==0 || fence.customMetadata?.kind!=='abandoned-upload') throw Error('storage_unavailable');
 }else{
  await bucket.delete(originalKey(row));
  if(await bucket.head(originalKey(row))) throw Error('storage_unavailable');
 }
 await db.batch([
  db.prepare("DELETE FROM documents WHERE id=? AND EXISTS(SELECT 1 FROM upload_reservations WHERE id=? AND status='deleting')").bind(row.id,row.id),
  db.prepare("DELETE FROM upload_reservations WHERE id=? AND status='deleting'").bind(row.id)
 ]);
}
export async function cleanupUploads(db,bucket,now){
 if(!bucket) return {cleaned:0,failed:0};
 const candidates=await db.prepare("SELECT id FROM upload_reservations WHERE (status='reserved' AND expires_at<=?) OR status='deleting' ORDER BY expires_at,id LIMIT 25").bind(now).all();
 let cleaned=0,failed=0;
 for(const {id} of candidates.results){
  try{
   const row=await db.prepare("UPDATE upload_reservations SET status='deleting' WHERE id=? AND ((status='reserved' AND expires_at<=?) OR status='deleting') RETURNING *").bind(id,now).first();
   if(!row)continue;
   await finishDeletion(db,bucket,row);cleaned++;
  }catch{failed++;}
 }
 return {cleaned,failed};
}
