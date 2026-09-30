CREATE TABLE invitations (
  token_digest TEXT PRIMARY KEY NOT NULL CHECK (length(token_digest) = 64),
  workspace_id TEXT NOT NULL REFERENCES workspaces(id),
  email TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('owner', 'member')),
  created_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL CHECK (expires_at > created_at),
  redeemed_by TEXT REFERENCES users(id),
  redeemed_at INTEGER,
  CHECK ((redeemed_by IS NULL AND redeemed_at IS NULL) OR
         (redeemed_by IS NOT NULL AND redeemed_at IS NOT NULL))
);
CREATE INDEX invitations_expiry ON invitations(expires_at);
CREATE TABLE login_transactions (
  browser_digest TEXT PRIMARY KEY NOT NULL CHECK (length(browser_digest) = 64),
  state_digest TEXT NOT NULL UNIQUE CHECK (length(state_digest) = 64),
  nonce TEXT NOT NULL,
  code_verifier TEXT NOT NULL,
  invitation_digest TEXT REFERENCES invitations(token_digest),
  created_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL CHECK (expires_at > created_at AND expires_at <= created_at + 600)
);
CREATE INDEX login_transactions_expiry ON login_transactions(expires_at);
