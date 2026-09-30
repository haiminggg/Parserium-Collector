// One bounded batch per invocation. Never loop until the database is empty.
export async function cleanupAuth(db,now) {
  const results=await db.batch([
    db.prepare(`DELETE FROM sessions WHERE token_digest IN
      (SELECT token_digest FROM sessions WHERE expires_at<=? ORDER BY expires_at LIMIT 100)`).bind(now),
    db.prepare(`DELETE FROM login_transactions WHERE browser_digest IN
      (SELECT browser_digest FROM login_transactions WHERE expires_at<=? ORDER BY expires_at LIMIT 100)`).bind(now),
  ]);
  return {sessions:results[0].meta.changes,transactions:results[1].meta.changes};
}
