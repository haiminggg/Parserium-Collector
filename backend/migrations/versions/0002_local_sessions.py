from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_local_sessions"
down_revision: str | None = "0001_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pairing_codes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code_digest", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code_digest"),
    )
    op.create_index(
        "ix_pairing_codes_expires_at",
        "pairing_codes",
        ["expires_at"],
        unique=False,
    )
    op.create_table(
        "local_sessions",
        sa.Column("token_digest", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("token_digest"),
    )
    op.create_index(
        "ix_local_sessions_last_seen_at",
        "local_sessions",
        ["last_seen_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_local_sessions_last_seen_at", table_name="local_sessions")
    op.drop_table("local_sessions")
    op.drop_index("ix_pairing_codes_expires_at", table_name="pairing_codes")
    op.drop_table("pairing_codes")
