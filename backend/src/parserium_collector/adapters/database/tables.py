from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    Numeric,
    PrimaryKeyConstraint,
    String,
    Table,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)

metadata = MetaData()

worker_heartbeats = Table(
    "worker_heartbeats",
    metadata,
    Column("worker_id", String(128), primary_key=True),
    Column("last_seen_at", DateTime(timezone=True), nullable=False),
)

pairing_codes = Table(
    "pairing_codes",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("code_digest", String(64), nullable=False, unique=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False, index=True),
    Column("used_at", DateTime(timezone=True)),
)

users = Table(
    "users",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("email", String(320), nullable=False),
    Column("normalized_email", String(320), nullable=False),
    Column("display_name", String(255)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("disabled_at", DateTime(timezone=True)),
)

workspaces = Table(
    "workspaces",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("name", String(120), nullable=False),
    Column("is_local", Boolean, nullable=False),
    Column(
        "discovery_concurrency_limit",
        Integer,
        nullable=False,
        server_default=text("1"),
    ),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        "discovery_concurrency_limit >= 1 AND discovery_concurrency_limit <= 5",
        name="ck_workspaces_discovery_concurrency_limit",
    ),
    Index(
        "uq_workspaces_single_local",
        "is_local",
        unique=True,
        postgresql_where=text("is_local IS TRUE"),
    ),
)

oidc_identities = Table(
    "oidc_identities",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("user_id", Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("issuer", String(2048), nullable=False),
    Column("subject", String(255), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("issuer", "subject", name="uq_oidc_identities_issuer_subject"),
)

workspace_memberships = Table(
    "workspace_memberships",
    metadata,
    Column("workspace_id", Uuid, ForeignKey("workspaces.id", ondelete="CASCADE")),
    Column("user_id", Uuid, ForeignKey("users.id", ondelete="CASCADE")),
    Column("role", String(16), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("role IN ('owner', 'member')", name="ck_workspace_memberships_role"),
    PrimaryKeyConstraint("workspace_id", "user_id", name="pk_workspace_memberships"),
)

workspace_invitations = Table(
    "workspace_invitations",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("workspace_id", Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
    Column("normalized_email", String(320), nullable=False),
    Column("token_digest", String(64), nullable=False, unique=True),
    Column("role", String(16), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("accepted_at", DateTime(timezone=True)),
    Column("revoked_at", DateTime(timezone=True)),
    Column("invited_by_user_id", Uuid, ForeignKey("users.id", ondelete="SET NULL")),
    CheckConstraint("role IN ('owner', 'member')", name="ck_workspace_invitations_role"),
    Index("ix_workspace_invitations_workspace_email", "workspace_id", "normalized_email"),
)

hosted_sessions = Table(
    "hosted_sessions",
    metadata,
    Column("token_digest", String(64), primary_key=True),
    Column("user_id", Uuid, nullable=False),
    Column("workspace_id", Uuid, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("last_seen_at", DateTime(timezone=True), nullable=False),
    Column("revoked_at", DateTime(timezone=True)),
    ForeignKeyConstraint(
        ["workspace_id", "user_id"],
        ["workspace_memberships.workspace_id", "workspace_memberships.user_id"],
        name="fk_hosted_sessions_membership",
        ondelete="CASCADE",
    ),
    Index("ix_hosted_sessions_user_last_seen", "user_id", "last_seen_at"),
)

local_sessions = Table(
    "local_sessions",
    metadata,
    Column("token_digest", String(64), primary_key=True),
    Column("workspace_id", Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("last_seen_at", DateTime(timezone=True), nullable=False, index=True),
    Column("revoked_at", DateTime(timezone=True)),
)

documents = Table(
    "documents",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("workspace_id", Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
    Column("sha256", String(64), nullable=False),
    Column("document_type", String(8), nullable=False),
    Column("media_type", String(128), nullable=False),
    Column("size_bytes", BigInteger, nullable=False),
    Column("safe_filename", String(255), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("deleted_at", DateTime(timezone=True)),
    CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="ck_documents_sha256"),
    CheckConstraint("document_type IN ('pdf', 'docx')", name="ck_documents_type"),
    CheckConstraint("size_bytes >= 0", name="ck_documents_size_nonnegative"),
    UniqueConstraint("workspace_id", "sha256", name="uq_documents_workspace_sha256"),
    UniqueConstraint("id", "workspace_id", name="uq_documents_id_workspace"),
    Index("ix_documents_workspace", "workspace_id"),
    Index(
        "ix_documents_workspace_live_created",
        "workspace_id",
        "created_at",
        postgresql_where=text("deleted_at IS NULL"),
    ),
)

collection_jobs = Table(
    "collection_jobs",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("workspace_id", Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
    Column("created_by_user_id", Uuid, ForeignKey("users.id", ondelete="SET NULL")),
    Column("source_url", Text, nullable=False),
    Column("title", String(500)),
    Column("expected_document_type", String(8), nullable=False),
    Column("status", String(16), nullable=False),
    Column("attempt_count", Integer, nullable=False),
    Column("available_at", DateTime(timezone=True), nullable=False),
    Column("claimed_by", String(128)),
    Column("lease_expires_at", DateTime(timezone=True)),
    Column("bytes_downloaded", BigInteger, nullable=False),
    Column("content_length", BigInteger),
    Column("document_id", Uuid),
    Column("error_code", String(64)),
    Column("error_detail", String(500)),
    Column("error_retryable", Boolean),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("started_at", DateTime(timezone=True)),
    Column("completed_at", DateTime(timezone=True)),
    Column("history_deleted_at", DateTime(timezone=True)),
    CheckConstraint(
        "status IN ('queued', 'downloading', 'validating', 'completed', 'duplicate', 'failed')",
        name="ck_collection_jobs_status",
    ),
    CheckConstraint(
        "expected_document_type IN ('pdf', 'docx')",
        name="ck_collection_jobs_type",
    ),
    CheckConstraint("attempt_count >= 0", name="ck_collection_jobs_attempts"),
    CheckConstraint("bytes_downloaded >= 0", name="ck_collection_jobs_bytes"),
    CheckConstraint(
        "content_length IS NULL OR content_length >= 0",
        name="ck_collection_jobs_content_length",
    ),
    ForeignKeyConstraint(
        ["document_id", "workspace_id"],
        ["documents.id", "documents.workspace_id"],
        name="fk_collection_jobs_document_workspace",
    ),
    Index("ix_collection_jobs_claim", "status", "available_at"),
    Index("ix_collection_jobs_created_at", "created_at"),
    Index("ix_collection_jobs_workspace", "workspace_id"),
    Index("ix_collection_jobs_activity", "workspace_id", "history_deleted_at", "created_at"),
)

document_exports = Table(
    "document_exports",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("workspace_id", Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
    Column("created_by_user_id", Uuid, ForeignKey("users.id", ondelete="SET NULL")),
    Column("document_id", Uuid, nullable=False),
    Column("relative_directory", String(500), nullable=False),
    Column("target_filename", String(255), nullable=False),
    Column("exported_relative_path", String(1024)),
    Column("status", String(16), nullable=False),
    Column("attempt_count", Integer, nullable=False),
    Column("available_at", DateTime(timezone=True), nullable=False),
    Column("claimed_by", String(128)),
    Column("lease_expires_at", DateTime(timezone=True)),
    Column("error_code", String(64)),
    Column("error_detail", String(500)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("completed_at", DateTime(timezone=True)),
    Column("history_deleted_at", DateTime(timezone=True)),
    CheckConstraint(
        "status IN ('queued', 'exporting', 'completed', 'failed')",
        name="ck_document_exports_status",
    ),
    CheckConstraint("attempt_count >= 0", name="ck_document_exports_attempts"),
    ForeignKeyConstraint(
        ["document_id", "workspace_id"],
        ["documents.id", "documents.workspace_id"],
        name="fk_document_exports_document_workspace",
    ),
    Index("ix_document_exports_claim", "status", "available_at"),
    Index("ix_document_exports_workspace", "workspace_id"),
    Index("ix_document_exports_activity", "workspace_id", "history_deleted_at", "created_at"),
)

firecrawl_connections = Table(
    "firecrawl_connections",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("workspace_id", Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
    Column("name", String(120), nullable=False),
    Column("normalized_name", String(120), nullable=False),
    Column("connection_type", String(16), nullable=False),
    Column("normalized_base_url", String(2048)),
    Column("credential_envelope", JSON(none_as_null=True)),
    Column("credential_revision", Integer, nullable=False),
    Column("validated_revision", Integer),
    Column("validation_succeeded", Boolean),
    Column("capability_profile", JSON(none_as_null=True)),
    Column("last_validation_attempt_at", DateTime(timezone=True)),
    Column("last_validation_success_at", DateTime(timezone=True)),
    Column("last_failure_category", String(64)),
    Column("enabled", Boolean, nullable=False),
    Column("is_default", Boolean, nullable=False),
    Column("created_by_user_id", Uuid, ForeignKey("users.id", ondelete="SET NULL")),
    Column("updated_by_user_id", Uuid, ForeignKey("users.id", ondelete="SET NULL")),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("deleted_at", DateTime(timezone=True)),
    CheckConstraint(
        "connection_type IN ('cloud', 'remote')",
        name="ck_firecrawl_connections_type",
    ),
    CheckConstraint(
        "credential_revision > 0",
        name="ck_firecrawl_connections_credential_revision",
    ),
    CheckConstraint(
        "validated_revision IS NULL OR "
        "(validated_revision > 0 AND validated_revision <= credential_revision)",
        name="ck_firecrawl_connections_validated_revision",
    ),
    CheckConstraint(
        "deleted_at IS NOT NULL OR "
        "(connection_type = 'cloud' AND normalized_base_url IS NULL) OR "
        "(connection_type = 'remote' AND normalized_base_url LIKE 'https://%')",
        name="ck_firecrawl_connections_live_endpoint",
    ),
    CheckConstraint(
        "(deleted_at IS NULL AND credential_envelope IS NOT NULL) OR "
        "(deleted_at IS NOT NULL AND credential_envelope IS NULL "
        "AND normalized_base_url IS NULL AND enabled IS FALSE AND is_default IS FALSE)",
        name="ck_firecrawl_connections_lifecycle",
    ),
    UniqueConstraint(
        "id",
        "workspace_id",
        name="uq_firecrawl_connections_id_workspace",
    ),
    Index(
        "uq_firecrawl_connections_workspace_name_live",
        "workspace_id",
        "normalized_name",
        unique=True,
        postgresql_where=text("deleted_at IS NULL"),
    ),
    Index(
        "uq_firecrawl_connections_workspace_default_live",
        "workspace_id",
        unique=True,
        postgresql_where=text("deleted_at IS NULL AND is_default IS TRUE"),
    ),
    Index("ix_firecrawl_connections_workspace", "workspace_id"),
)

discovery_analysis_sessions = Table(
    "discovery_analysis_sessions",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("workspace_id", Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
    Column(
        "owner_session_digest",
        String(64),
        ForeignKey("local_sessions.token_digest", ondelete="CASCADE"),
        nullable=True,
    ),
    Column("created_by_user_id", Uuid, ForeignKey("users.id", ondelete="SET NULL")),
    Column("firecrawl_connection_id", Uuid),
    Column("firecrawl_connection_name_snapshot", String(120)),
    Column("firecrawl_connection_type_snapshot", String(16)),
    Column("query", String(500), nullable=False),
    Column("document_types", JSON, nullable=False),
    Column("include_domains", JSON, nullable=False),
    Column("exclude_domains", JSON, nullable=False),
    Column("tables_required", Boolean, nullable=False),
    Column("provider_search_ids", JSON, nullable=False),
    Column("status", String(16), nullable=False),
    Column("candidate_count", Integer, nullable=False),
    Column("session_byte_limit", BigInteger, nullable=False),
    Column("bytes_downloaded", BigInteger, nullable=False),
    Column("cancellation_requested", Boolean, nullable=False),
    Column("error_code", String(64)),
    Column("error_detail", String(500)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("completed_at", DateTime(timezone=True)),
    Column("history_deleted_at", DateTime(timezone=True)),
    Column("request_fingerprint", String(64)),
    Column("request_fingerprint_version", Integer),
    Column("result_limit", Integer),
    Column("job_stage", String(16), nullable=False),
    Column("cache_reusable_until", DateTime(timezone=True)),
    Column("discovery_claimed_by", String(128)),
    Column("discovery_lease_expires_at", DateTime(timezone=True)),
    Column("provider_request_started_at", DateTime(timezone=True)),
    Column("firecrawl_credential_revision_snapshot", Integer),
    Column("creation_reason", String(16), nullable=False),
    CheckConstraint(
        "status IN ('queued', 'running', 'completed', 'cancelled', 'failed')",
        name="ck_analysis_sessions_status",
    ),
    CheckConstraint("candidate_count >= 0", name="ck_analysis_sessions_candidates"),
    CheckConstraint("session_byte_limit > 0", name="ck_analysis_sessions_byte_limit"),
    CheckConstraint(
        "bytes_downloaded >= 0 AND bytes_downloaded <= session_byte_limit",
        name="ck_analysis_sessions_bytes",
    ),
    CheckConstraint(
        "firecrawl_connection_type_snapshot IS NULL OR "
        "firecrawl_connection_type_snapshot IN ('cloud', 'remote')",
        name="ck_analysis_sessions_firecrawl_connection_type",
    ),
    CheckConstraint(
        "(request_fingerprint IS NULL AND request_fingerprint_version IS NULL) OR "
        "(request_fingerprint IS NOT NULL AND request_fingerprint_version IS NOT NULL "
        "AND request_fingerprint ~ '^[0-9a-f]{64}$' "
        "AND request_fingerprint_version > 0)",
        name="ck_analysis_sessions_request_fingerprint",
    ),
    CheckConstraint(
        "result_limit IS NULL OR result_limit BETWEEN 1 AND 30",
        name="ck_analysis_sessions_result_limit",
    ),
    CheckConstraint(
        "firecrawl_credential_revision_snapshot IS NULL OR "
        "firecrawl_credential_revision_snapshot > 0",
        name="ck_analysis_sessions_credential_revision_snapshot",
    ),
    CheckConstraint(
        "creation_reason IN ('initial', 'retry', 'force_refresh')",
        name="ck_analysis_sessions_creation_reason",
    ),
    CheckConstraint(
        "(status = 'queued' AND job_stage = 'queued') OR "
        "(status = 'running' AND job_stage IN ('discovering', 'analyzing')) OR "
        "(status = 'completed' AND job_stage = 'completed') OR "
        "(status = 'cancelled' AND job_stage = 'cancelled') OR "
        "(status = 'failed' AND job_stage = 'failed')",
        name="ck_analysis_sessions_status_stage",
    ),
    CheckConstraint(
        "(discovery_claimed_by IS NULL AND discovery_lease_expires_at IS NULL) OR "
        "(discovery_claimed_by IS NOT NULL AND discovery_lease_expires_at IS NOT NULL)",
        name="ck_analysis_sessions_discovery_ownership",
    ),
    CheckConstraint(
        "provider_request_started_at IS NULL OR status <> 'queued'",
        name="ck_analysis_sessions_provider_request_ownership",
    ),
    CheckConstraint(
        "cache_reusable_until IS NULL OR "
        "(status = 'completed' AND job_stage = 'completed' AND completed_at IS NOT NULL "
        "AND completed_at <= cache_reusable_until AND cache_reusable_until <= expires_at)",
        name="ck_analysis_sessions_cache_reuse",
    ),
    ForeignKeyConstraint(
        ["firecrawl_connection_id", "workspace_id"],
        ["firecrawl_connections.id", "firecrawl_connections.workspace_id"],
        name="fk_analysis_sessions_firecrawl_connection_workspace",
    ),
    UniqueConstraint(
        "id",
        "workspace_id",
        name="uq_analysis_sessions_id_workspace",
    ),
    Index("ix_analysis_sessions_owner_created", "owner_session_digest", "created_at"),
    Index("ix_analysis_sessions_expiry", "status", "expires_at"),
    Index("ix_discovery_analysis_sessions_workspace", "workspace_id"),
    Index(
        "ix_discovery_analysis_sessions_activity",
        "workspace_id",
        "history_deleted_at",
        "created_at",
    ),
    Index(
        "uq_analysis_sessions_active_fingerprint",
        "workspace_id",
        "request_fingerprint",
        unique=True,
        postgresql_where=text(
            "status IN ('queued', 'running') AND request_fingerprint IS NOT NULL"
        ),
    ),
    Index(
        "ix_analysis_sessions_discovery_claim",
        "status",
        "created_at",
        postgresql_where=text("status = 'queued'"),
    ),
    Index(
        "ix_analysis_sessions_cache_reuse",
        "workspace_id",
        "request_fingerprint",
        "cache_reusable_until",
        postgresql_where=text(
            "status = 'completed' AND request_fingerprint IS NOT NULL "
            "AND cache_reusable_until IS NOT NULL"
        ),
    ),
)

discovery_job_events = Table(
    "discovery_job_events",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("workspace_id", Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
    Column("session_id", Uuid),
    Column("actor_kind", String(16), nullable=False),
    Column("actor_user_id", Uuid, ForeignKey("users.id", ondelete="SET NULL")),
    Column("event_type", String(48), nullable=False),
    Column("safe_metadata", JSON(none_as_null=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        "actor_kind IN ('user', 'worker', 'system')",
        name="ck_discovery_job_events_actor_kind",
    ),
    CheckConstraint(
        "event_type IN ('job_created', 'active_reused', 'cached_reused', "
        "'force_refresh_created', 'job_claimed', 'provider_request_started', "
        "'analysis_started', 'job_completed', 'job_failed', "
        "'cancellation_requested', 'job_cancelled', 'retry_created', "
        "'concurrency_updated')",
        name="ck_discovery_job_events_event_type",
    ),
    ForeignKeyConstraint(
        ["session_id", "workspace_id"],
        ["discovery_analysis_sessions.id", "discovery_analysis_sessions.workspace_id"],
        name="fk_discovery_job_events_session_workspace",
        ondelete="CASCADE",
    ),
    Index("ix_discovery_job_events_workspace_created", "workspace_id", "created_at"),
)

discovery_job_links = Table(
    "discovery_job_links",
    metadata,
    Column("child_session_id", Uuid, primary_key=True),
    Column("workspace_id", Uuid, nullable=False),
    Column("parent_session_id", Uuid, nullable=False),
    Column("reason", String(16), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        "reason IN ('retry', 'force_refresh')",
        name="ck_discovery_job_links_reason",
    ),
    CheckConstraint(
        "child_session_id <> parent_session_id",
        name="ck_discovery_job_links_distinct_sessions",
    ),
    ForeignKeyConstraint(
        ["child_session_id", "workspace_id"],
        ["discovery_analysis_sessions.id", "discovery_analysis_sessions.workspace_id"],
        name="fk_discovery_job_links_child_workspace",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["parent_session_id", "workspace_id"],
        ["discovery_analysis_sessions.id", "discovery_analysis_sessions.workspace_id"],
        name="fk_discovery_job_links_parent_workspace",
        ondelete="CASCADE",
    ),
    Index("ix_discovery_job_links_parent", "workspace_id", "parent_session_id"),
)

candidate_analyses = Table(
    "candidate_analyses",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("workspace_id", Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
    Column("session_id", Uuid, nullable=False),
    Column("ordinal", Integer, nullable=False),
    Column("source_url", Text, nullable=False),
    Column("title", String(500)),
    Column("description", String(2000)),
    Column("document_type", String(8), nullable=False),
    Column("status", String(16), nullable=False),
    Column("attempt_count", Integer, nullable=False),
    Column("available_at", DateTime(timezone=True), nullable=False),
    Column("claimed_by", String(128)),
    Column("lease_expires_at", DateTime(timezone=True)),
    Column("bytes_downloaded", BigInteger, nullable=False),
    Column("content_length", BigInteger),
    Column("sha256", String(64)),
    Column("media_type", String(128)),
    Column("safe_filename", String(255)),
    Column("page_count", Integer),
    Column("analyzed_page_count", Integer, nullable=False),
    Column("table_count", Integer, nullable=False),
    Column("table_count_lower_bound", Boolean, nullable=False),
    Column("preview_page_num", Integer),
    Column("preview_width", Integer),
    Column("preview_height", Integer),
    Column("error_code", String(64)),
    Column("error_detail", String(500)),
    Column("error_retryable", Boolean),
    Column("promoted_document_id", Uuid),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("started_at", DateTime(timezone=True)),
    Column("completed_at", DateTime(timezone=True)),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("history_deleted_at", DateTime(timezone=True)),
    CheckConstraint(
        "status IN ('queued', 'downloading', 'validating', 'converting', 'parsing', "
        "'ready', 'no_tables', 'partial', 'failed', 'cancelled', 'promoted')",
        name="ck_candidate_analyses_status",
    ),
    CheckConstraint("document_type IN ('pdf', 'docx')", name="ck_candidate_analyses_type"),
    CheckConstraint("ordinal >= 0", name="ck_candidate_analyses_ordinal"),
    CheckConstraint("attempt_count >= 0", name="ck_candidate_analyses_attempts"),
    CheckConstraint("bytes_downloaded >= 0", name="ck_candidate_analyses_bytes"),
    CheckConstraint(
        "content_length IS NULL OR content_length >= 0",
        name="ck_candidate_analyses_content_length",
    ),
    CheckConstraint(
        "sha256 IS NULL OR sha256 ~ '^[0-9a-f]{64}$'",
        name="ck_candidate_analyses_sha256",
    ),
    CheckConstraint(
        "page_count IS NULL OR page_count >= 0",
        name="ck_candidate_analyses_page_count",
    ),
    CheckConstraint(
        "analyzed_page_count >= 0 AND (page_count IS NULL OR analyzed_page_count <= page_count)",
        name="ck_candidate_analyses_page_counts",
    ),
    CheckConstraint("table_count >= 0", name="ck_candidate_analyses_table_count"),
    CheckConstraint(
        "preview_page_num IS NULL OR preview_page_num >= 1",
        name="ck_candidate_analyses_preview_page",
    ),
    CheckConstraint(
        "(preview_width IS NULL AND preview_height IS NULL) OR "
        "(preview_width > 0 AND preview_height > 0)",
        name="ck_candidate_analyses_preview_dimensions",
    ),
    ForeignKeyConstraint(
        ["session_id", "workspace_id"],
        ["discovery_analysis_sessions.id", "discovery_analysis_sessions.workspace_id"],
        name="fk_candidate_analyses_session_workspace",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["promoted_document_id", "workspace_id"],
        ["documents.id", "documents.workspace_id"],
        name="fk_candidate_analyses_document_workspace",
    ),
    UniqueConstraint("id", "workspace_id", name="uq_candidate_analyses_id_workspace"),
    UniqueConstraint("session_id", "ordinal", name="uq_candidate_analyses_session_ordinal"),
    Index("ix_candidate_analyses_claim", "status", "available_at"),
    Index("ix_candidate_analyses_session_ordinal", "session_id", "ordinal"),
    Index("ix_candidate_analyses_expiry", "status", "expires_at"),
    Index("ix_candidate_analyses_workspace", "workspace_id"),
    Index(
        "ix_candidate_analyses_activity",
        "workspace_id",
        "history_deleted_at",
        "created_at",
    ),
)

candidate_tables = Table(
    "candidate_tables",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("workspace_id", Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
    Column("candidate_analysis_id", Uuid, nullable=False),
    Column("page_num", Integer, nullable=False),
    Column("table_index", Integer, nullable=False),
    Column("x", Numeric(12, 4), nullable=False),
    Column("y", Numeric(12, 4), nullable=False),
    Column("width", Numeric(12, 4), nullable=False),
    Column("height", Numeric(12, 4), nullable=False),
    Column("cells", JSON, nullable=False),
    Column("markdown", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("page_num >= 1", name="ck_candidate_tables_page"),
    CheckConstraint("table_index >= 0", name="ck_candidate_tables_index"),
    CheckConstraint("x >= 0 AND y >= 0", name="ck_candidate_tables_coordinates"),
    CheckConstraint("width > 0 AND height > 0", name="ck_candidate_tables_dimensions"),
    ForeignKeyConstraint(
        ["candidate_analysis_id", "workspace_id"],
        ["candidate_analyses.id", "candidate_analyses.workspace_id"],
        name="fk_candidate_tables_candidate_workspace",
        ondelete="CASCADE",
    ),
    UniqueConstraint(
        "candidate_analysis_id",
        "page_num",
        "table_index",
        name="uq_candidate_tables_page_index",
    ),
    Index("ix_candidate_tables_candidate_page", "candidate_analysis_id", "page_num"),
    Index("ix_candidate_tables_workspace", "workspace_id"),
)

artifact_objects = Table(
    "artifact_objects",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("workspace_id", Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
    Column("storage_key", String(512), nullable=False),
    Column("media_type", String(128), nullable=False),
    Column("size_bytes", BigInteger),
    Column("sha256", String(64)),
    Column("state", String(32), nullable=False),
    Column("available_at", DateTime(timezone=True)),
    Column("delete_attempt_count", Integer, nullable=False),
    Column("delete_available_at", DateTime(timezone=True)),
    Column("delete_claimed_by", String(128)),
    Column("delete_lease_expires_at", DateTime(timezone=True)),
    Column("failure_code", String(64)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("deleted_at", DateTime(timezone=True)),
    CheckConstraint(
        "state IN ('legacy_pending', 'uploading', 'available', 'deleting', "
        "'delete_failed', 'deleted')",
        name="ck_artifact_objects_state",
    ),
    CheckConstraint(
        "(state = 'legacy_pending' AND size_bytes IS NULL AND sha256 IS NULL) OR "
        "(state <> 'legacy_pending' AND size_bytes IS NOT NULL AND sha256 IS NOT NULL)",
        name="ck_artifact_objects_metadata",
    ),
    CheckConstraint(
        "size_bytes IS NULL OR size_bytes >= 0",
        name="ck_artifact_objects_size_nonnegative",
    ),
    CheckConstraint(
        "sha256 IS NULL OR sha256 ~ '^[0-9a-f]{64}$'",
        name="ck_artifact_objects_sha256",
    ),
    CheckConstraint(
        "delete_attempt_count >= 0",
        name="ck_artifact_objects_delete_attempts",
    ),
    UniqueConstraint(
        "workspace_id",
        "storage_key",
        name="uq_artifact_objects_workspace_storage_key",
    ),
    UniqueConstraint("id", "workspace_id", name="uq_artifact_objects_id_workspace"),
    Index("ix_artifact_objects_workspace_state", "workspace_id", "state"),
    Index(
        "ix_artifact_objects_delete_claim",
        "state",
        "delete_available_at",
        "delete_lease_expires_at",
    ),
    Index("ix_artifact_objects_state_created", "state", "created_at"),
)

artifact_references = Table(
    "artifact_references",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("workspace_id", Uuid, nullable=False),
    Column("artifact_object_id", Uuid, nullable=False),
    Column("candidate_analysis_id", Uuid),
    Column("document_id", Uuid),
    Column("kind", String(32), nullable=False),
    Column("lifecycle", String(16), nullable=False),
    Column("expires_at", DateTime(timezone=True)),
    Column("removed_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        "(candidate_analysis_id IS NOT NULL AND document_id IS NULL) OR "
        "(candidate_analysis_id IS NULL AND document_id IS NOT NULL)",
        name="ck_artifact_references_one_owner",
    ),
    CheckConstraint(
        "kind IN ('source_pdf', 'source_docx', 'converted_pdf', 'preview_png', 'stored_document')",
        name="ck_artifact_references_kind",
    ),
    CheckConstraint(
        "lifecycle IN ('temporary', 'persistent')",
        name="ck_artifact_references_lifecycle",
    ),
    CheckConstraint(
        "(lifecycle = 'temporary' AND expires_at IS NOT NULL) OR "
        "(lifecycle = 'persistent' AND expires_at IS NULL)",
        name="ck_artifact_references_expiry",
    ),
    CheckConstraint(
        "(document_id IS NOT NULL AND kind = 'stored_document') OR "
        "(candidate_analysis_id IS NOT NULL AND kind IN "
        "('source_pdf', 'source_docx', 'converted_pdf', 'preview_png'))",
        name="ck_artifact_references_owner_kind",
    ),
    ForeignKeyConstraint(
        ["artifact_object_id", "workspace_id"],
        ["artifact_objects.id", "artifact_objects.workspace_id"],
        name="fk_artifact_references_object_workspace",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["candidate_analysis_id", "workspace_id"],
        ["candidate_analyses.id", "candidate_analyses.workspace_id"],
        name="fk_artifact_references_candidate_workspace",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["document_id", "workspace_id"],
        ["documents.id", "documents.workspace_id"],
        name="fk_artifact_references_document_workspace",
        ondelete="CASCADE",
    ),
    Index("ix_artifact_references_workspace_object", "workspace_id", "artifact_object_id"),
    Index("ix_artifact_references_expiry", "lifecycle", "expires_at", "removed_at"),
    Index(
        "uq_artifact_references_live_candidate_kind",
        "candidate_analysis_id",
        "kind",
        unique=True,
        postgresql_where=text("removed_at IS NULL AND candidate_analysis_id IS NOT NULL"),
    ),
    Index(
        "uq_artifact_references_live_document_kind",
        "document_id",
        "kind",
        unique=True,
        postgresql_where=text("removed_at IS NULL AND document_id IS NOT NULL"),
    ),
)

workspace_storage_usage = Table(
    "workspace_storage_usage",
    metadata,
    Column(
        "workspace_id",
        Uuid,
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("retained_bytes", BigInteger, nullable=False),
    Column("retained_objects", BigInteger, nullable=False),
    Column("reconciled_at", DateTime(timezone=True)),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        "retained_bytes >= 0",
        name="ck_workspace_storage_usage_bytes_nonnegative",
    ),
    CheckConstraint(
        "retained_objects >= 0",
        name="ck_workspace_storage_usage_objects_nonnegative",
    ),
)
