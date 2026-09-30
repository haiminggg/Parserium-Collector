from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_firecrawl_connections"
down_revision: str | None = "0006_cloud_artifact_storage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "firecrawl_connections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("normalized_name", sa.String(length=120), nullable=False),
        sa.Column("connection_type", sa.String(length=16), nullable=False),
        sa.Column("normalized_base_url", sa.String(length=2048), nullable=True),
        sa.Column("credential_envelope", sa.JSON(), nullable=True),
        sa.Column("credential_revision", sa.Integer(), nullable=False),
        sa.Column("validated_revision", sa.Integer(), nullable=True),
        sa.Column("validation_succeeded", sa.Boolean(), nullable=True),
        sa.Column("capability_profile", sa.JSON(), nullable=True),
        sa.Column("last_validation_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_validation_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failure_category", sa.String(length=64), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("updated_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "connection_type IN ('cloud', 'remote')",
            name="ck_firecrawl_connections_type",
        ),
        sa.CheckConstraint(
            "credential_revision > 0",
            name="ck_firecrawl_connections_credential_revision",
        ),
        sa.CheckConstraint(
            "validated_revision IS NULL OR "
            "(validated_revision > 0 AND validated_revision <= credential_revision)",
            name="ck_firecrawl_connections_validated_revision",
        ),
        sa.CheckConstraint(
            "deleted_at IS NOT NULL OR "
            "(connection_type = 'cloud' AND normalized_base_url IS NULL) OR "
            "(connection_type = 'remote' AND normalized_base_url LIKE 'https://%')",
            name="ck_firecrawl_connections_live_endpoint",
        ),
        sa.CheckConstraint(
            "(deleted_at IS NULL AND credential_envelope IS NOT NULL) OR "
            "(deleted_at IS NOT NULL AND credential_envelope IS NULL "
            "AND normalized_base_url IS NULL AND enabled IS FALSE AND is_default IS FALSE)",
            name="ck_firecrawl_connections_lifecycle",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_firecrawl_connections_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_firecrawl_connections_created_by_user",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"],
            ["users.id"],
            name="fk_firecrawl_connections_updated_by_user",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_firecrawl_connections"),
        sa.UniqueConstraint(
            "id",
            "workspace_id",
            name="uq_firecrawl_connections_id_workspace",
        ),
    )
    op.create_index(
        "uq_firecrawl_connections_workspace_name_live",
        "firecrawl_connections",
        ["workspace_id", "normalized_name"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "uq_firecrawl_connections_workspace_default_live",
        "firecrawl_connections",
        ["workspace_id"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL AND is_default IS TRUE"),
    )
    op.create_index(
        "ix_firecrawl_connections_workspace",
        "firecrawl_connections",
        ["workspace_id"],
        unique=False,
    )

    op.add_column(
        "discovery_analysis_sessions",
        sa.Column("firecrawl_connection_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "discovery_analysis_sessions",
        sa.Column(
            "firecrawl_connection_name_snapshot",
            sa.String(length=120),
            nullable=True,
        ),
    )
    op.add_column(
        "discovery_analysis_sessions",
        sa.Column(
            "firecrawl_connection_type_snapshot",
            sa.String(length=16),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_analysis_sessions_firecrawl_connection_type",
        "discovery_analysis_sessions",
        "firecrawl_connection_type_snapshot IS NULL OR "
        "firecrawl_connection_type_snapshot IN ('cloud', 'remote')",
    )
    op.create_foreign_key(
        "fk_analysis_sessions_firecrawl_connection_workspace",
        "discovery_analysis_sessions",
        "firecrawl_connections",
        ["firecrawl_connection_id", "workspace_id"],
        ["id", "workspace_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_analysis_sessions_firecrawl_connection_workspace",
        "discovery_analysis_sessions",
        type_="foreignkey",
    )
    op.drop_constraint(
        "ck_analysis_sessions_firecrawl_connection_type",
        "discovery_analysis_sessions",
        type_="check",
    )
    op.drop_column("discovery_analysis_sessions", "firecrawl_connection_type_snapshot")
    op.drop_column("discovery_analysis_sessions", "firecrawl_connection_name_snapshot")
    op.drop_column("discovery_analysis_sessions", "firecrawl_connection_id")

    op.drop_index(
        "ix_firecrawl_connections_workspace",
        table_name="firecrawl_connections",
    )
    op.drop_index(
        "uq_firecrawl_connections_workspace_default_live",
        table_name="firecrawl_connections",
    )
    op.drop_index(
        "uq_firecrawl_connections_workspace_name_live",
        table_name="firecrawl_connections",
    )
    op.drop_table("firecrawl_connections")
