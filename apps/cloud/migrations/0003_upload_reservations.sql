CREATE TABLE upload_reservations (
 id TEXT PRIMARY KEY NOT NULL,
 workspace_id TEXT NOT NULL REFERENCES workspaces(id),
 request_id TEXT NOT NULL,
 filename TEXT NOT NULL,
 size_bytes INTEGER NOT NULL CHECK(size_bytes>0 AND size_bytes<=10485760),
 reserved_bytes INTEGER NOT NULL CHECK(reserved_bytes=size_bytes+10485760),
 status TEXT NOT NULL CHECK(status IN ('reserved','stored','deleting')),
 created_at INTEGER NOT NULL,
 expires_at INTEGER NOT NULL CHECK(expires_at=created_at+3600),
 UNIQUE(workspace_id,request_id)
);
CREATE INDEX upload_reservations_workspace ON upload_reservations(workspace_id);
CREATE INDEX upload_reservations_expiry ON upload_reservations(status,expires_at);
