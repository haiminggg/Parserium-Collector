from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "0005_hosted_identity_tenancy"
down_revision: str | None = "0004_document_analysis"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WORKSPACE_OWNED_TABLES = (
    "local_sessions",
    "documents",
    "collection_jobs",
    "document_exports",
    "discovery_analysis_sessions",
    "candidate_analyses",
    "candidate_tables",
)


def upgrade() -> None:
    migration_time = datetime.now(UTC)
    local_workspace_id = uuid4()

    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("normalized_email", sa.String(length=320), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
    )
    op.create_table(
        "workspaces",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("is_local", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_workspaces"),
    )
    op.create_index(
        "uq_workspaces_single_local",
        "workspaces",
        ["is_local"],
        unique=True,
        postgresql_where=sa.text("is_local IS TRUE"),
    )
    op.create_table(
        "oidc_identities",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("issuer", sa.String(length=2048), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_oidc_identities_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_oidc_identities"),
        sa.UniqueConstraint("issuer", "subject", name="uq_oidc_identities_issuer_subject"),
    )
    op.create_table(
        "workspace_memberships",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("role IN ('owner', 'member')", name="ck_workspace_memberships_role"),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_workspace_memberships_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_workspace_memberships_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "workspace_id",
            "user_id",
            name="pk_workspace_memberships",
        ),
    )
    op.create_table(
        "workspace_invitations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("normalized_email", sa.String(length=320), nullable=False),
        sa.Column("token_digest", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invited_by_user_id", sa.Uuid(), nullable=True),
        sa.CheckConstraint("role IN ('owner', 'member')", name="ck_workspace_invitations_role"),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_workspace_invitations_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["invited_by_user_id"],
            ["users.id"],
            name="fk_workspace_invitations_inviter",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_workspace_invitations"),
        sa.UniqueConstraint("token_digest", name="uq_workspace_invitations_token_digest"),
    )
    op.create_index(
        "ix_workspace_invitations_workspace_email",
        "workspace_invitations",
        ["workspace_id", "normalized_email"],
        unique=False,
    )
    op.create_table(
        "hosted_sessions",
        sa.Column("token_digest", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["workspace_id", "user_id"],
            ["workspace_memberships.workspace_id", "workspace_memberships.user_id"],
            name="fk_hosted_sessions_membership",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("token_digest", name="pk_hosted_sessions"),
    )
    op.create_index(
        "ix_hosted_sessions_user_last_seen",
        "hosted_sessions",
        ["user_id", "last_seen_at"],
        unique=False,
    )

    for table_name in WORKSPACE_OWNED_TABLES:
        op.add_column(table_name, sa.Column("workspace_id", sa.Uuid(), nullable=True))
    op.add_column(
        "discovery_analysis_sessions",
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
    )
    op.alter_column(
        "discovery_analysis_sessions",
        "owner_session_digest",
        existing_type=sa.String(length=64),
        nullable=True,
    )

    workspaces = sa.table(
        "workspaces",
        sa.column("id", sa.Uuid()),
        sa.column("name", sa.String()),
        sa.column("is_local", sa.Boolean()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    bind = op.get_bind()
    bind.execute(
        workspaces.insert().values(
            id=local_workspace_id,
            name="Local workspace",
            is_local=True,
            created_at=migration_time,
            updated_at=migration_time,
        )
    )
    for table_name in WORKSPACE_OWNED_TABLES:
        ownership_table = sa.table(
            table_name,
            sa.column("workspace_id", sa.Uuid()),
        )
        bind.execute(
            ownership_table.update().values(workspace_id=local_workspace_id)
        )
        op.alter_column(
            table_name,
            "workspace_id",
            existing_type=sa.Uuid(),
            nullable=False,
        )

    op.create_foreign_key(
        "fk_local_sessions_workspace",
        "local_sessions",
        "workspaces",
        ["workspace_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_documents_workspace",
        "documents",
        "workspaces",
        ["workspace_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_collection_jobs_workspace",
        "collection_jobs",
        "workspaces",
        ["workspace_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_document_exports_workspace",
        "document_exports",
        "workspaces",
        ["workspace_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_analysis_sessions_workspace",
        "discovery_analysis_sessions",
        "workspaces",
        ["workspace_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_analysis_sessions_creator",
        "discovery_analysis_sessions",
        "users",
        ["created_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_candidate_analyses_workspace",
        "candidate_analyses",
        "workspaces",
        ["workspace_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_candidate_tables_workspace",
        "candidate_tables",
        "workspaces",
        ["workspace_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.drop_constraint("documents_sha256_key", "documents", type_="unique")
    op.create_unique_constraint(
        "uq_documents_workspace_sha256",
        "documents",
        ["workspace_id", "sha256"],
    )
    op.create_unique_constraint("uq_documents_id_workspace", "documents", ["id", "workspace_id"])
    op.create_unique_constraint(
        "uq_analysis_sessions_id_workspace",
        "discovery_analysis_sessions",
        ["id", "workspace_id"],
    )
    op.create_unique_constraint(
        "uq_candidate_analyses_id_workspace",
        "candidate_analyses",
        ["id", "workspace_id"],
    )

    op.drop_constraint(
        "collection_jobs_document_id_fkey",
        "collection_jobs",
        type_="foreignkey",
    )
    op.drop_constraint(
        "document_exports_document_id_fkey",
        "document_exports",
        type_="foreignkey",
    )
    op.drop_constraint(
        "candidate_analyses_session_id_fkey",
        "candidate_analyses",
        type_="foreignkey",
    )
    op.drop_constraint(
        "candidate_analyses_promoted_document_id_fkey",
        "candidate_analyses",
        type_="foreignkey",
    )
    op.drop_constraint(
        "candidate_tables_candidate_analysis_id_fkey",
        "candidate_tables",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_collection_jobs_document_workspace",
        "collection_jobs",
        "documents",
        ["document_id", "workspace_id"],
        ["id", "workspace_id"],
    )
    op.create_foreign_key(
        "fk_document_exports_document_workspace",
        "document_exports",
        "documents",
        ["document_id", "workspace_id"],
        ["id", "workspace_id"],
    )
    op.create_foreign_key(
        "fk_candidate_analyses_session_workspace",
        "candidate_analyses",
        "discovery_analysis_sessions",
        ["session_id", "workspace_id"],
        ["id", "workspace_id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_candidate_analyses_document_workspace",
        "candidate_analyses",
        "documents",
        ["promoted_document_id", "workspace_id"],
        ["id", "workspace_id"],
    )
    op.create_foreign_key(
        "fk_candidate_tables_candidate_workspace",
        "candidate_tables",
        "candidate_analyses",
        ["candidate_analysis_id", "workspace_id"],
        ["id", "workspace_id"],
        ondelete="CASCADE",
    )

    for table_name in WORKSPACE_OWNED_TABLES:
        op.create_index(
            f"ix_{table_name}_workspace",
            table_name,
            ["workspace_id"],
            unique=False,
        )


def downgrade() -> None:
    for table_name in reversed(WORKSPACE_OWNED_TABLES):
        op.drop_index(f"ix_{table_name}_workspace", table_name=table_name)

    op.drop_constraint(
        "fk_candidate_tables_candidate_workspace",
        "candidate_tables",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_candidate_analyses_document_workspace",
        "candidate_analyses",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_candidate_analyses_session_workspace",
        "candidate_analyses",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_document_exports_document_workspace",
        "document_exports",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_collection_jobs_document_workspace",
        "collection_jobs",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "collection_jobs_document_id_fkey",
        "collection_jobs",
        "documents",
        ["document_id"],
        ["id"],
    )
    op.create_foreign_key(
        "document_exports_document_id_fkey",
        "document_exports",
        "documents",
        ["document_id"],
        ["id"],
    )
    op.create_foreign_key(
        "candidate_analyses_session_id_fkey",
        "candidate_analyses",
        "discovery_analysis_sessions",
        ["session_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "candidate_analyses_promoted_document_id_fkey",
        "candidate_analyses",
        "documents",
        ["promoted_document_id"],
        ["id"],
    )
    op.create_foreign_key(
        "candidate_tables_candidate_analysis_id_fkey",
        "candidate_tables",
        "candidate_analyses",
        ["candidate_analysis_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.drop_constraint(
        "uq_candidate_analyses_id_workspace",
        "candidate_analyses",
        type_="unique",
    )
    op.drop_constraint(
        "uq_analysis_sessions_id_workspace",
        "discovery_analysis_sessions",
        type_="unique",
    )
    op.drop_constraint("uq_documents_id_workspace", "documents", type_="unique")
    op.drop_constraint("uq_documents_workspace_sha256", "documents", type_="unique")
    op.create_unique_constraint("documents_sha256_key", "documents", ["sha256"])

    workspace_foreign_keys = (
        ("candidate_tables", "fk_candidate_tables_workspace"),
        ("candidate_analyses", "fk_candidate_analyses_workspace"),
        ("discovery_analysis_sessions", "fk_analysis_sessions_creator"),
        ("discovery_analysis_sessions", "fk_analysis_sessions_workspace"),
        ("document_exports", "fk_document_exports_workspace"),
        ("collection_jobs", "fk_collection_jobs_workspace"),
        ("documents", "fk_documents_workspace"),
        ("local_sessions", "fk_local_sessions_workspace"),
    )
    for table_name, constraint_name in workspace_foreign_keys:
        op.drop_constraint(constraint_name, table_name, type_="foreignkey")

    op.alter_column(
        "discovery_analysis_sessions",
        "owner_session_digest",
        existing_type=sa.String(length=64),
        nullable=False,
    )
    op.drop_column("discovery_analysis_sessions", "created_by_user_id")
    for table_name in reversed(WORKSPACE_OWNED_TABLES):
        op.drop_column(table_name, "workspace_id")

    op.drop_index("ix_hosted_sessions_user_last_seen", table_name="hosted_sessions")
    op.drop_table("hosted_sessions")
    op.drop_index(
        "ix_workspace_invitations_workspace_email",
        table_name="workspace_invitations",
    )
    op.drop_table("workspace_invitations")
    op.drop_table("workspace_memberships")
    op.drop_table("oidc_identities")
    op.drop_index("uq_workspaces_single_local", table_name="workspaces")
    op.drop_table("workspaces")
    op.drop_table("users")
