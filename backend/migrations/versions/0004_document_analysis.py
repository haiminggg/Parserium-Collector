from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_document_analysis"
down_revision: str | None = "0003_document_acquisition"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "discovery_analysis_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_session_digest", sa.String(length=64), nullable=False),
        sa.Column("query", sa.String(length=500), nullable=False),
        sa.Column("document_types", sa.JSON(), nullable=False),
        sa.Column("include_domains", sa.JSON(), nullable=False),
        sa.Column("exclude_domains", sa.JSON(), nullable=False),
        sa.Column("tables_required", sa.Boolean(), nullable=False),
        sa.Column("provider_search_ids", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("candidate_count", sa.Integer(), nullable=False),
        sa.Column("session_byte_limit", sa.BigInteger(), nullable=False),
        sa.Column("bytes_downloaded", sa.BigInteger(), nullable=False),
        sa.Column("cancellation_requested", sa.Boolean(), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_detail", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'cancelled', 'failed')",
            name="ck_analysis_sessions_status",
        ),
        sa.CheckConstraint("candidate_count >= 0", name="ck_analysis_sessions_candidates"),
        sa.CheckConstraint("session_byte_limit > 0", name="ck_analysis_sessions_byte_limit"),
        sa.CheckConstraint(
            "bytes_downloaded >= 0 AND bytes_downloaded <= session_byte_limit",
            name="ck_analysis_sessions_bytes",
        ),
        sa.ForeignKeyConstraint(
            ["owner_session_digest"],
            ["local_sessions.token_digest"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_analysis_sessions_owner_created",
        "discovery_analysis_sessions",
        ["owner_session_digest", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_analysis_sessions_expiry",
        "discovery_analysis_sessions",
        ["status", "expires_at"],
        unique=False,
    )

    op.create_table(
        "candidate_analyses",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=True),
        sa.Column("description", sa.String(length=2000), nullable=True),
        sa.Column("document_type", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_by", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("bytes_downloaded", sa.BigInteger(), nullable=False),
        sa.Column("content_length", sa.BigInteger(), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("media_type", sa.String(length=128), nullable=True),
        sa.Column("safe_filename", sa.String(length=255), nullable=True),
        sa.Column("temporary_storage_key", sa.String(length=512), nullable=True),
        sa.Column("converted_pdf_storage_key", sa.String(length=512), nullable=True),
        sa.Column("preview_storage_key", sa.String(length=512), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("analyzed_page_count", sa.Integer(), nullable=False),
        sa.Column("table_count", sa.Integer(), nullable=False),
        sa.Column("table_count_lower_bound", sa.Boolean(), nullable=False),
        sa.Column("preview_page_num", sa.Integer(), nullable=True),
        sa.Column("preview_width", sa.Integer(), nullable=True),
        sa.Column("preview_height", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_detail", sa.String(length=500), nullable=True),
        sa.Column("error_retryable", sa.Boolean(), nullable=True),
        sa.Column("promoted_document_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('queued', 'downloading', 'validating', 'converting', 'parsing', "
            "'ready', 'no_tables', 'partial', 'failed', 'cancelled', 'promoted')",
            name="ck_candidate_analyses_status",
        ),
        sa.CheckConstraint(
            "document_type IN ('pdf', 'docx')",
            name="ck_candidate_analyses_type",
        ),
        sa.CheckConstraint("ordinal >= 0", name="ck_candidate_analyses_ordinal"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_candidate_analyses_attempts"),
        sa.CheckConstraint("bytes_downloaded >= 0", name="ck_candidate_analyses_bytes"),
        sa.CheckConstraint(
            "content_length IS NULL OR content_length >= 0",
            name="ck_candidate_analyses_content_length",
        ),
        sa.CheckConstraint(
            "sha256 IS NULL OR sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_candidate_analyses_sha256",
        ),
        sa.CheckConstraint(
            "page_count IS NULL OR page_count >= 0",
            name="ck_candidate_analyses_page_count",
        ),
        sa.CheckConstraint(
            "analyzed_page_count >= 0 AND "
            "(page_count IS NULL OR analyzed_page_count <= page_count)",
            name="ck_candidate_analyses_page_counts",
        ),
        sa.CheckConstraint("table_count >= 0", name="ck_candidate_analyses_table_count"),
        sa.CheckConstraint(
            "preview_page_num IS NULL OR preview_page_num >= 1",
            name="ck_candidate_analyses_preview_page",
        ),
        sa.CheckConstraint(
            "(preview_width IS NULL AND preview_height IS NULL) OR "
            "(preview_width > 0 AND preview_height > 0)",
            name="ck_candidate_analyses_preview_dimensions",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["discovery_analysis_sessions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["promoted_document_id"], ["documents.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "session_id",
            "ordinal",
            name="uq_candidate_analyses_session_ordinal",
        ),
        sa.UniqueConstraint("temporary_storage_key"),
        sa.UniqueConstraint("converted_pdf_storage_key"),
        sa.UniqueConstraint("preview_storage_key"),
    )
    op.create_index(
        "ix_candidate_analyses_claim",
        "candidate_analyses",
        ["status", "available_at"],
        unique=False,
    )
    op.create_index(
        "ix_candidate_analyses_session_ordinal",
        "candidate_analyses",
        ["session_id", "ordinal"],
        unique=False,
    )
    op.create_index(
        "ix_candidate_analyses_expiry",
        "candidate_analyses",
        ["status", "expires_at"],
        unique=False,
    )

    op.create_table(
        "candidate_tables",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("candidate_analysis_id", sa.Uuid(), nullable=False),
        sa.Column("page_num", sa.Integer(), nullable=False),
        sa.Column("table_index", sa.Integer(), nullable=False),
        sa.Column("x", sa.Numeric(precision=12, scale=4), nullable=False),
        sa.Column("y", sa.Numeric(precision=12, scale=4), nullable=False),
        sa.Column("width", sa.Numeric(precision=12, scale=4), nullable=False),
        sa.Column("height", sa.Numeric(precision=12, scale=4), nullable=False),
        sa.Column("cells", sa.JSON(), nullable=False),
        sa.Column("markdown", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("page_num >= 1", name="ck_candidate_tables_page"),
        sa.CheckConstraint("table_index >= 0", name="ck_candidate_tables_index"),
        sa.CheckConstraint("x >= 0 AND y >= 0", name="ck_candidate_tables_coordinates"),
        sa.CheckConstraint("width > 0 AND height > 0", name="ck_candidate_tables_dimensions"),
        sa.ForeignKeyConstraint(
            ["candidate_analysis_id"],
            ["candidate_analyses.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "candidate_analysis_id",
            "page_num",
            "table_index",
            name="uq_candidate_tables_page_index",
        ),
    )
    op.create_index(
        "ix_candidate_tables_candidate_page",
        "candidate_tables",
        ["candidate_analysis_id", "page_num"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_candidate_tables_candidate_page", table_name="candidate_tables")
    op.drop_table("candidate_tables")
    op.drop_index("ix_candidate_analyses_expiry", table_name="candidate_analyses")
    op.drop_index("ix_candidate_analyses_session_ordinal", table_name="candidate_analyses")
    op.drop_index("ix_candidate_analyses_claim", table_name="candidate_analyses")
    op.drop_table("candidate_analyses")
    op.drop_index("ix_analysis_sessions_expiry", table_name="discovery_analysis_sessions")
    op.drop_index(
        "ix_analysis_sessions_owner_created",
        table_name="discovery_analysis_sessions",
    )
    op.drop_table("discovery_analysis_sessions")
