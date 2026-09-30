from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_document_acquisition"
down_revision: str | None = "0002_local_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("document_type", sa.String(length=8), nullable=False),
        sa.Column("media_type", sa.String(length=128), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("safe_filename", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="ck_documents_sha256"),
        sa.CheckConstraint("document_type IN ('pdf', 'docx')", name="ck_documents_type"),
        sa.CheckConstraint("size_bytes >= 0", name="ck_documents_size_nonnegative"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sha256"),
        sa.UniqueConstraint("storage_key"),
    )
    op.create_table(
        "collection_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=True),
        sa.Column("expected_document_type", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_by", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("bytes_downloaded", sa.BigInteger(), nullable=False),
        sa.Column("content_length", sa.BigInteger(), nullable=True),
        sa.Column("document_id", sa.Uuid(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_detail", sa.String(length=500), nullable=True),
        sa.Column("error_retryable", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued', 'downloading', 'validating', 'completed', 'duplicate', 'failed')",
            name="ck_collection_jobs_status",
        ),
        sa.CheckConstraint(
            "expected_document_type IN ('pdf', 'docx')",
            name="ck_collection_jobs_type",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_collection_jobs_attempts"),
        sa.CheckConstraint("bytes_downloaded >= 0", name="ck_collection_jobs_bytes"),
        sa.CheckConstraint(
            "content_length IS NULL OR content_length >= 0",
            name="ck_collection_jobs_content_length",
        ),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_collection_jobs_claim",
        "collection_jobs",
        ["status", "available_at"],
        unique=False,
    )
    op.create_index(
        "ix_collection_jobs_created_at",
        "collection_jobs",
        ["created_at"],
        unique=False,
    )
    op.create_table(
        "document_exports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("relative_directory", sa.String(length=500), nullable=False),
        sa.Column("target_filename", sa.String(length=255), nullable=False),
        sa.Column("exported_relative_path", sa.String(length=1024), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_by", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_detail", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued', 'exporting', 'completed', 'failed')",
            name="ck_document_exports_status",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_document_exports_attempts"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_document_exports_claim",
        "document_exports",
        ["status", "available_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_document_exports_claim", table_name="document_exports")
    op.drop_table("document_exports")
    op.drop_index("ix_collection_jobs_created_at", table_name="collection_jobs")
    op.drop_index("ix_collection_jobs_claim", table_name="collection_jobs")
    op.drop_table("collection_jobs")
    op.drop_table("documents")
