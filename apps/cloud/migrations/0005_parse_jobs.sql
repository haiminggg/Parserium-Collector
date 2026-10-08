ALTER TABLE documents ADD COLUMN validation_status TEXT NOT NULL DEFAULT 'awaiting_validation' CHECK(validation_status IN ('awaiting_validation','valid','invalid'));
ALTER TABLE documents ADD COLUMN validation_page_count INTEGER CHECK(validation_page_count IS NULL OR (validation_page_count>=1 AND validation_page_count<=20));
ALTER TABLE documents ADD COLUMN validation_error_code TEXT CHECK(validation_error_code IS NULL OR validation_error_code IN ('invalid_pdf','encrypted_pdf','page_limit_exceeded'));

CREATE TABLE parse_jobs (
 id TEXT PRIMARY KEY NOT NULL,
 workspace_id TEXT NOT NULL REFERENCES workspaces(id),
 document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
 request_id TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('pending_dispatch','queued','running','succeeded','failed')),
 attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count>=0 AND attempt_count<=2),
 day_start INTEGER NOT NULL,
 reserved_runtime_ms INTEGER NOT NULL DEFAULT 120000 CHECK(reserved_runtime_ms=120000),
 runtime_ms INTEGER NOT NULL DEFAULT 0 CHECK(runtime_ms>=0 AND runtime_ms<=120000),
 fence_token TEXT,
 lease_expires_at INTEGER,
 output_key TEXT,
 output_size_bytes INTEGER CHECK(output_size_bytes IS NULL OR (output_size_bytes>=0 AND output_size_bytes<=10485760)),
 page_count INTEGER CHECK(page_count IS NULL OR (page_count>=1 AND page_count<=20)),
 table_count INTEGER CHECK(table_count IS NULL OR table_count>=0),
 error_code TEXT CHECK(error_code IS NULL OR length(error_code)<=64),
 created_at INTEGER NOT NULL,
 updated_at INTEGER NOT NULL,
 completed_at INTEGER,
 UNIQUE(workspace_id,request_id),
 UNIQUE(document_id),
 CHECK(day_start=created_at-(created_at%86400)),
 CHECK(completed_at IS NULL OR completed_at>=created_at)
);
CREATE INDEX parse_jobs_workspace_created ON parse_jobs(workspace_id,created_at DESC,id DESC);
CREATE INDEX parse_jobs_dispatch ON parse_jobs(status,updated_at,id);
CREATE INDEX parse_jobs_lease ON parse_jobs(status,lease_expires_at,id);
CREATE INDEX parse_jobs_day_budget ON parse_jobs(day_start,status);
