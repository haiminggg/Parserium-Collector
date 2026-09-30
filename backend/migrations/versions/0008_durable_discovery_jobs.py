from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_durable_discovery_jobs"
down_revision: str | None = "0007_firecrawl_connections"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workspaces",
        sa.Column(
            "discovery_concurrency_limit",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_workspaces_discovery_concurrency_limit",
        "workspaces",
        "discovery_concurrency_limit >= 1 AND discovery_concurrency_limit <= 5",
    )

    op.add_column(
        "discovery_analysis_sessions",
        sa.Column("request_fingerprint", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "discovery_analysis_sessions",
        sa.Column("request_fingerprint_version", sa.Integer(), nullable=True),
    )
    op.add_column(
        "discovery_analysis_sessions",
        sa.Column("result_limit", sa.Integer(), nullable=True),
    )
    op.add_column(
        "discovery_analysis_sessions",
        sa.Column("job_stage", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "discovery_analysis_sessions",
        sa.Column("cache_reusable_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "discovery_analysis_sessions",
        sa.Column("discovery_claimed_by", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "discovery_analysis_sessions",
        sa.Column("discovery_lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "discovery_analysis_sessions",
        sa.Column("provider_request_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "discovery_analysis_sessions",
        sa.Column(
            "firecrawl_credential_revision_snapshot",
            sa.Integer(),
            nullable=True,
        ),
    )
    op.add_column(
        "discovery_analysis_sessions",
        sa.Column("creation_reason", sa.String(length=16), nullable=True),
    )

    op.execute("UPDATE discovery_analysis_sessions SET status = 'running' WHERE status = 'queued'")
    op.execute(
        "UPDATE discovery_analysis_sessions SET job_stage = CASE "
        "WHEN status = 'running' THEN 'analyzing' "
        "WHEN status = 'completed' THEN 'completed' "
        "WHEN status = 'cancelled' THEN 'cancelled' "
        "WHEN status = 'failed' THEN 'failed' END"
    )
    op.execute("UPDATE discovery_analysis_sessions SET creation_reason = 'initial'")
    op.alter_column("discovery_analysis_sessions", "job_stage", nullable=False)
    op.alter_column("discovery_analysis_sessions", "creation_reason", nullable=False)

    op.create_check_constraint(
        "ck_analysis_sessions_request_fingerprint",
        "discovery_analysis_sessions",
        "(request_fingerprint IS NULL AND request_fingerprint_version IS NULL) OR "
        "(request_fingerprint IS NOT NULL AND request_fingerprint_version IS NOT NULL "
        "AND request_fingerprint ~ '^[0-9a-f]{64}$' "
        "AND request_fingerprint_version > 0)",
    )
    op.create_check_constraint(
        "ck_analysis_sessions_result_limit",
        "discovery_analysis_sessions",
        "result_limit IS NULL OR result_limit BETWEEN 1 AND 30",
    )
    op.create_check_constraint(
        "ck_analysis_sessions_credential_revision_snapshot",
        "discovery_analysis_sessions",
        "firecrawl_credential_revision_snapshot IS NULL OR "
        "firecrawl_credential_revision_snapshot > 0",
    )
    op.create_check_constraint(
        "ck_analysis_sessions_creation_reason",
        "discovery_analysis_sessions",
        "creation_reason IN ('initial', 'retry', 'force_refresh')",
    )
    op.create_check_constraint(
        "ck_analysis_sessions_status_stage",
        "discovery_analysis_sessions",
        "(status = 'queued' AND job_stage = 'queued') OR "
        "(status = 'running' AND job_stage IN ('discovering', 'analyzing')) OR "
        "(status = 'completed' AND job_stage = 'completed') OR "
        "(status = 'cancelled' AND job_stage = 'cancelled') OR "
        "(status = 'failed' AND job_stage = 'failed')",
    )
    op.create_check_constraint(
        "ck_analysis_sessions_discovery_ownership",
        "discovery_analysis_sessions",
        "(discovery_claimed_by IS NULL AND discovery_lease_expires_at IS NULL) OR "
        "(discovery_claimed_by IS NOT NULL AND discovery_lease_expires_at IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_analysis_sessions_provider_request_ownership",
        "discovery_analysis_sessions",
        "provider_request_started_at IS NULL OR status <> 'queued'",
    )
    op.create_check_constraint(
        "ck_analysis_sessions_cache_reuse",
        "discovery_analysis_sessions",
        "cache_reusable_until IS NULL OR "
        "(status = 'completed' AND job_stage = 'completed' AND completed_at IS NOT NULL "
        "AND completed_at <= cache_reusable_until AND cache_reusable_until <= expires_at)",
    )
    op.create_index(
        "uq_analysis_sessions_active_fingerprint",
        "discovery_analysis_sessions",
        ["workspace_id", "request_fingerprint"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('queued', 'running') AND request_fingerprint IS NOT NULL"
        ),
    )
    op.create_index(
        "ix_analysis_sessions_discovery_claim",
        "discovery_analysis_sessions",
        ["status", "created_at"],
        unique=False,
        postgresql_where=sa.text("status = 'queued'"),
    )
    op.create_index(
        "ix_analysis_sessions_cache_reuse",
        "discovery_analysis_sessions",
        ["workspace_id", "request_fingerprint", "cache_reusable_until"],
        unique=False,
        postgresql_where=sa.text(
            "status = 'completed' AND request_fingerprint IS NOT NULL "
            "AND cache_reusable_until IS NOT NULL"
        ),
    )

    op.create_table(
        "discovery_job_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column("actor_kind", sa.String(length=16), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("event_type", sa.String(length=48), nullable=False),
        sa.Column("safe_metadata", sa.JSON(none_as_null=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "actor_kind IN ('user', 'worker', 'system')",
            name="ck_discovery_job_events_actor_kind",
        ),
        sa.CheckConstraint(
            "event_type IN ('job_created', 'active_reused', 'cached_reused', "
            "'force_refresh_created', 'job_claimed', 'provider_request_started', "
            "'analysis_started', 'job_completed', 'job_failed', "
            "'cancellation_requested', 'job_cancelled', 'retry_created', "
            "'concurrency_updated')",
            name="ck_discovery_job_events_event_type",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_discovery_job_events_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name="fk_discovery_job_events_actor_user",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["session_id", "workspace_id"],
            ["discovery_analysis_sessions.id", "discovery_analysis_sessions.workspace_id"],
            name="fk_discovery_job_events_session_workspace",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_discovery_job_events"),
    )
    op.create_index(
        "ix_discovery_job_events_workspace_created",
        "discovery_job_events",
        ["workspace_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "discovery_job_links",
        sa.Column("child_session_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("parent_session_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "reason IN ('retry', 'force_refresh')",
            name="ck_discovery_job_links_reason",
        ),
        sa.CheckConstraint(
            "child_session_id <> parent_session_id",
            name="ck_discovery_job_links_distinct_sessions",
        ),
        sa.ForeignKeyConstraint(
            ["child_session_id", "workspace_id"],
            ["discovery_analysis_sessions.id", "discovery_analysis_sessions.workspace_id"],
            name="fk_discovery_job_links_child_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["parent_session_id", "workspace_id"],
            ["discovery_analysis_sessions.id", "discovery_analysis_sessions.workspace_id"],
            name="fk_discovery_job_links_parent_workspace",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("child_session_id", name="pk_discovery_job_links"),
    )
    op.create_index(
        "ix_discovery_job_links_parent",
        "discovery_job_links",
        ["workspace_id", "parent_session_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_discovery_job_links_parent", table_name="discovery_job_links")
    op.drop_table("discovery_job_links")
    op.drop_index(
        "ix_discovery_job_events_workspace_created",
        table_name="discovery_job_events",
    )
    op.drop_table("discovery_job_events")

    op.drop_index(
        "ix_analysis_sessions_cache_reuse",
        table_name="discovery_analysis_sessions",
    )
    op.drop_index(
        "ix_analysis_sessions_discovery_claim",
        table_name="discovery_analysis_sessions",
    )
    op.drop_index(
        "uq_analysis_sessions_active_fingerprint",
        table_name="discovery_analysis_sessions",
    )
    for constraint_name in (
        "ck_analysis_sessions_cache_reuse",
        "ck_analysis_sessions_provider_request_ownership",
        "ck_analysis_sessions_discovery_ownership",
        "ck_analysis_sessions_status_stage",
        "ck_analysis_sessions_creation_reason",
        "ck_analysis_sessions_credential_revision_snapshot",
        "ck_analysis_sessions_result_limit",
        "ck_analysis_sessions_request_fingerprint",
    ):
        op.drop_constraint(
            constraint_name,
            "discovery_analysis_sessions",
            type_="check",
        )
    for column_name in (
        "creation_reason",
        "firecrawl_credential_revision_snapshot",
        "provider_request_started_at",
        "discovery_lease_expires_at",
        "discovery_claimed_by",
        "cache_reusable_until",
        "job_stage",
        "result_limit",
        "request_fingerprint_version",
        "request_fingerprint",
    ):
        op.drop_column("discovery_analysis_sessions", column_name)

    op.drop_constraint(
        "ck_workspaces_discovery_concurrency_limit",
        "workspaces",
        type_="check",
    )
    op.drop_column("workspaces", "discovery_concurrency_limit")
