import {digest,randomToken} from './security.mjs';

// Operator-only primitive. Never expose directly as an unauthenticated Worker route.
// The operator adapter must supply an authorized database binding.
export async function issueInvitation(db,{workspaceId,email,role='member',ttlSeconds=86400},now) {
  if (typeof workspaceId!=='string' || !workspaceId || workspaceId.length>128 ||
      typeof email!=='string' || email.length>320 || email.trim()!==email ||
      !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email) ||
      !['owner','member'].includes(role) ||
      !Number.isSafeInteger(ttlSeconds) || ttlSeconds<1 || ttlSeconds>604800 ||
      !Number.isSafeInteger(now) || now<0 || !Number.isSafeInteger(now+ttlSeconds)) {
    throw new Error('Invalid invitation settings');
  }
  const token=randomToken(),expiresAt=now+ttlSeconds;
  const result=await db.prepare(`INSERT INTO invitations
    (token_digest,workspace_id,email,role,created_at,expires_at)
    SELECT ?,id,?,?,?,? FROM workspaces WHERE id=?`)
    .bind(await digest(token),email,role,now,expiresAt,workspaceId).run();
  if(result.meta.changes!==1) throw new Error('Workspace not found');
  return {token,expiresAt};
}
