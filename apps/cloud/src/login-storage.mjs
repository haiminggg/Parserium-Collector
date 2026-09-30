// Server-internal primitives. No public route accepts user IDs or verified claims.
export async function consumeLogin(db, browserDigest, stateDigest, now) {
  return db.prepare(`DELETE FROM login_transactions
    WHERE browser_digest=? AND state_digest=? AND expires_at>?
    RETURNING nonce, code_verifier, invitation_digest`)
    .bind(browserDigest, stateDigest, now).first();
}

// Caller must first validate Google OIDC and persist the verified identity/email.
export async function redeemInvitation(db, invitationDigest, userId, now) {
  // D1 batch is transactional: membership and consumption succeed together.
  const results = await db.batch([
    db.prepare(`INSERT INTO memberships (workspace_id,user_id,role)
      SELECT i.workspace_id,u.id,i.role FROM invitations i JOIN users u ON u.id=?
      WHERE i.token_digest=? AND i.email=u.email COLLATE NOCASE
        AND i.expires_at>? AND i.redeemed_at IS NULL
      ON CONFLICT (workspace_id,user_id) DO NOTHING`).bind(userId,invitationDigest,now),
    db.prepare(`UPDATE invitations SET redeemed_by=?,redeemed_at=?
      WHERE token_digest=? AND expires_at>? AND redeemed_at IS NULL
        AND EXISTS (SELECT 1 FROM users u WHERE u.id=? AND u.email=invitations.email COLLATE NOCASE)
        AND EXISTS (SELECT 1 FROM memberships m WHERE m.user_id=? AND m.workspace_id=invitations.workspace_id)`)
      .bind(userId,now,invitationDigest,now,userId,userId),
  ]);
  return results[1].meta.changes === 1;
}
