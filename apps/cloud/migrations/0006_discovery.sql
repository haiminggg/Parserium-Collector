ALTER TABLE documents ADD COLUMN source_url TEXT;
ALTER TABLE documents ADD COLUMN format_error_code TEXT CHECK(format_error_code IS NULL OR format_error_code='invalid_docx');
CREATE TABLE firecrawl_connections (
 workspace_id TEXT PRIMARY KEY NOT NULL REFERENCES workspaces(id),
 encrypted_key TEXT NOT NULL,
 updated_at INTEGER NOT NULL
);
CREATE TABLE discovery_searches (
 id TEXT PRIMARY KEY NOT NULL,
 workspace_id TEXT NOT NULL REFERENCES workspaces(id),
 request_id TEXT NOT NULL,
 query TEXT NOT NULL CHECK(length(query)<=500),
 file_type TEXT NOT NULL CHECK(file_type IN ('pdf','docx','all')),
 result_limit INTEGER NOT NULL CHECK(result_limit>=1 AND result_limit<=20),
 status TEXT NOT NULL CHECK(status IN ('running','succeeded','failed')),
 results_json TEXT,
 error_code TEXT,
 created_at INTEGER NOT NULL,
 updated_at INTEGER NOT NULL,
 UNIQUE(workspace_id,request_id)
);
CREATE INDEX discovery_searches_workspace ON discovery_searches(workspace_id,created_at DESC);
