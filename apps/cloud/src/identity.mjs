import {digest, randomToken} from './security.mjs';
import {redeemInvitation} from './login-storage.mjs';

// Internal only. The caller must cryptographically validate the Google ID token first.
export async function completeIdentity(db,claims,invitationDigest,now) {
  if (claims?.iss !== 'https://accounts.google.com' || claims.email_verified !== true ||
      typeof claims.sub !== 'string' || !claims.sub.trim() || claims.sub.length > 255 ||
      typeof claims.email !== 'string' || !claims.email.includes('@') || claims.email.length > 320) return null;
  const candidate = crypto.randomUUID();
  // Do not persist arbitrary uninvited identities. Returning membership is keyed by issuer+subject.
  await db.prepare(`INSERT INTO users (id,issuer,subject,email,created_at)
    SELECT ?,?,?,?,? WHERE
      EXISTS (SELECT 1 FROM users u JOIN memberships m ON m.user_id=u.id WHERE u.issuer=? AND u.subject=?)
      OR EXISTS (SELECT 1 FROM invitations WHERE token_digest=? AND email=? COLLATE NOCASE
        AND expires_at>? AND redeemed_at IS NULL)
    ON CONFLICT (issuer,subject) DO UPDATE SET email=excluded.email`)
    .bind(candidate,claims.iss,claims.sub,claims.email,now,claims.iss,claims.sub,invitationDigest,claims.email,now).run();
  const user = await db.prepare('SELECT id FROM users WHERE issuer=? AND subject=?').bind(claims.iss,claims.sub).first();
  if (!user) return null;
  if (invitationDigest && !await redeemInvitation(db,invitationDigest,user.id,now)) return null;
  const token = randomToken(), csrf = randomToken();
  const result = await db.prepare(`INSERT INTO sessions (token_digest,user_id,csrf_digest,created_at,expires_at)
    SELECT ?,?,?,?,? WHERE EXISTS (SELECT 1 FROM memberships WHERE user_id=?)`)
    .bind(await digest(token),user.id,await digest(csrf),now,now+86400,user.id).run();
  return result.meta.changes === 1 ? {token,csrf} : null;
}
