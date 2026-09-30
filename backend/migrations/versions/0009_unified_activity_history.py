from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_unified_activity_history"
down_revision: str | None = "0008_durable_discovery_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "collection_jobs",
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_collection_jobs_created_by_user",
        "collection_jobs",
        "users",
        ["created_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column(
        "document_exports",
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_document_exports_created_by_user",
        "document_exports",
        "users",
        ["created_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    for table_name in (
        "discovery_analysis_sessions",
        "candidate_analyses",
        "collection_jobs",
        "document_exports",
    ):
        op.add_column(
            table_name,
            sa.Column("history_deleted_at", sa.DateTime(timezone=True), nullable=True),
        )
    op.create_index(
        "ix_discovery_analysis_sessions_activity",
        "discovery_analysis_sessions",
        ["workspace_id", "history_deleted_at", "created_at"],
    )
    op.create_index(
        "ix_candidate_analyses_activity",
        "candidate_analyses",
        ["workspace_id", "history_deleted_at", "created_at"],
    )
    op.create_index(
        "ix_collection_jobs_activity",
        "collection_jobs",
        ["workspace_id", "history_deleted_at", "created_at"],
    )
    op.create_index(
        "ix_document_exports_activity",
        "document_exports",
        ["workspace_id", "history_deleted_at", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_document_exports_activity", table_name="document_exports")
    op.drop_index("ix_collection_jobs_activity", table_name="collection_jobs")
    op.drop_index("ix_candidate_analyses_activity", table_name="candidate_analyses")
    op.drop_index(
        "ix_discovery_analysis_sessions_activity",
        table_name="discovery_analysis_sessions",
    )
    for table_name in (
        "document_exports",
        "collection_jobs",
        "candidate_analyses",
        "discovery_analysis_sessions",
    ):
        op.drop_column(table_name, "history_deleted_at")
    op.drop_constraint(
        "fk_document_exports_created_by_user",
        "document_exports",
        type_="foreignkey",
    )
    op.drop_column("document_exports", "created_by_user_id")
    op.drop_constraint(
        "fk_collection_jobs_created_by_user",
        "collection_jobs",
        type_="foreignkey",
    )
    op.drop_column("collection_jobs", "created_by_user_id")
