from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision: str = "0006_cloud_artifact_storage"
down_revision: str | None = "0005_hosted_identity_tenancy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _create_registry_tables() -> None:
    op.create_table(
        "artifact_objects",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("media_type", sa.String(length=128), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "delete_attempt_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("delete_available_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delete_claimed_by", sa.String(length=128), nullable=True),
        sa.Column("delete_lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "state IN ('legacy_pending', 'uploading', 'available', 'deleting', "
            "'delete_failed', 'deleted')",
            name="ck_artifact_objects_state",
        ),
        sa.CheckConstraint(
            "(state = 'legacy_pending' AND size_bytes IS NULL AND sha256 IS NULL) OR "
            "(state <> 'legacy_pending' AND size_bytes IS NOT NULL AND sha256 IS NOT NULL)",
            name="ck_artifact_objects_metadata",
        ),
        sa.CheckConstraint(
            "size_bytes IS NULL OR size_bytes >= 0",
            name="ck_artifact_objects_size_nonnegative",
        ),
        sa.CheckConstraint(
            "sha256 IS NULL OR sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_artifact_objects_sha256",
        ),
        sa.CheckConstraint(
            "delete_attempt_count >= 0",
            name="ck_artifact_objects_delete_attempts",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_artifact_objects_workspace",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_artifact_objects"),
        sa.UniqueConstraint(
            "workspace_id",
            "storage_key",
            name="uq_artifact_objects_workspace_storage_key",
        ),
        sa.UniqueConstraint(
            "id",
            "workspace_id",
            name="uq_artifact_objects_id_workspace",
        ),
    )
    op.create_index(
        "ix_artifact_objects_workspace_state",
        "artifact_objects",
        ["workspace_id", "state"],
        unique=False,
    )
    op.create_index(
        "ix_artifact_objects_delete_claim",
        "artifact_objects",
        ["state", "delete_available_at", "delete_lease_expires_at"],
        unique=False,
    )
    op.create_index(
        "ix_artifact_objects_state_created",
        "artifact_objects",
        ["state", "created_at"],
        unique=False,
    )

    op.create_table(
        "artifact_references",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("artifact_object_id", sa.Uuid(), nullable=False),
        sa.Column("candidate_analysis_id", sa.Uuid(), nullable=True),
        sa.Column("document_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("lifecycle", sa.String(length=16), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(candidate_analysis_id IS NOT NULL AND document_id IS NULL) OR "
            "(candidate_analysis_id IS NULL AND document_id IS NOT NULL)",
            name="ck_artifact_references_one_owner",
        ),
        sa.CheckConstraint(
            "kind IN ('source_pdf', 'source_docx', 'converted_pdf', 'preview_png', "
            "'stored_document')",
            name="ck_artifact_references_kind",
        ),
        sa.CheckConstraint(
            "lifecycle IN ('temporary', 'persistent')",
            name="ck_artifact_references_lifecycle",
        ),
        sa.CheckConstraint(
            "(lifecycle = 'temporary' AND expires_at IS NOT NULL) OR "
            "(lifecycle = 'persistent' AND expires_at IS NULL)",
            name="ck_artifact_references_expiry",
        ),
        sa.CheckConstraint(
            "(document_id IS NOT NULL AND kind = 'stored_document') OR "
            "(candidate_analysis_id IS NOT NULL AND kind IN "
            "('source_pdf', 'source_docx', 'converted_pdf', 'preview_png'))",
            name="ck_artifact_references_owner_kind",
        ),
        sa.ForeignKeyConstraint(
            ["artifact_object_id", "workspace_id"],
            ["artifact_objects.id", "artifact_objects.workspace_id"],
            name="fk_artifact_references_object_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_analysis_id", "workspace_id"],
            ["candidate_analyses.id", "candidate_analyses.workspace_id"],
            name="fk_artifact_references_candidate_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_id", "workspace_id"],
            ["documents.id", "documents.workspace_id"],
            name="fk_artifact_references_document_workspace",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_artifact_references"),
    )
    op.create_index(
        "ix_artifact_references_workspace_object",
        "artifact_references",
        ["workspace_id", "artifact_object_id"],
        unique=False,
    )
    op.create_index(
        "ix_artifact_references_expiry",
        "artifact_references",
        ["lifecycle", "expires_at", "removed_at"],
        unique=False,
    )
    op.create_index(
        "uq_artifact_references_live_candidate_kind",
        "artifact_references",
        ["candidate_analysis_id", "kind"],
        unique=True,
        postgresql_where=sa.text("removed_at IS NULL AND candidate_analysis_id IS NOT NULL"),
    )
    op.create_index(
        "uq_artifact_references_live_document_kind",
        "artifact_references",
        ["document_id", "kind"],
        unique=True,
        postgresql_where=sa.text("removed_at IS NULL AND document_id IS NOT NULL"),
    )

    op.create_table(
        "workspace_storage_usage",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column(
            "retained_bytes",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "retained_objects",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("reconciled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "retained_bytes >= 0",
            name="ck_workspace_storage_usage_bytes_nonnegative",
        ),
        sa.CheckConstraint(
            "retained_objects >= 0",
            name="ck_workspace_storage_usage_objects_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_workspace_storage_usage_workspace",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("workspace_id", name="pk_workspace_storage_usage"),
    )


def _ensure_object(
    connection: Connection,
    object_ids: dict[tuple[UUID, str], UUID],
    *,
    workspace_id: UUID,
    storage_key: str,
    media_type: str,
    state: str,
    size_bytes: int | None,
    sha256: str | None,
    created_at: datetime,
    updated_at: datetime,
) -> UUID:
    identity = (workspace_id, storage_key)
    existing_id = object_ids.get(identity)
    if existing_id is not None:
        return existing_id

    object_id = uuid4()
    connection.execute(
        sa.text(
            "INSERT INTO artifact_objects "
            "(id, workspace_id, storage_key, media_type, size_bytes, sha256, state, "
            "available_at, delete_attempt_count, created_at, updated_at) VALUES "
            "(:id, :workspace_id, :storage_key, :media_type, :size_bytes, :sha256, "
            ":state, :available_at, 0, :created_at, :updated_at)"
        ),
        {
            "id": object_id,
            "workspace_id": workspace_id,
            "storage_key": storage_key,
            "media_type": media_type,
            "size_bytes": size_bytes,
            "sha256": sha256,
            "state": state,
            "available_at": created_at if state == "available" else None,
            "created_at": created_at,
            "updated_at": updated_at,
        },
    )
    object_ids[identity] = object_id
    return object_id


def _insert_reference(
    connection: Connection,
    *,
    workspace_id: UUID,
    artifact_object_id: UUID,
    candidate_analysis_id: UUID | None,
    document_id: UUID | None,
    kind: str,
    lifecycle: str,
    expires_at: datetime | None,
    created_at: datetime,
) -> None:
    connection.execute(
        sa.text(
            "INSERT INTO artifact_references "
            "(id, workspace_id, artifact_object_id, candidate_analysis_id, document_id, "
            "kind, lifecycle, expires_at, created_at) VALUES "
            "(:id, :workspace_id, :artifact_object_id, :candidate_analysis_id, "
            ":document_id, :kind, :lifecycle, :expires_at, :created_at)"
        ),
        {
            "id": uuid4(),
            "workspace_id": workspace_id,
            "artifact_object_id": artifact_object_id,
            "candidate_analysis_id": candidate_analysis_id,
            "document_id": document_id,
            "kind": kind,
            "lifecycle": lifecycle,
            "expires_at": expires_at,
            "created_at": created_at,
        },
    )


def _backfill_registry() -> None:
    connection = op.get_bind()
    object_ids: dict[tuple[UUID, str], UUID] = {}

    documents = connection.execute(
        sa.text(
            "SELECT id, workspace_id, storage_key, media_type, size_bytes, sha256, "
            "created_at FROM documents ORDER BY created_at, id"
        )
    ).mappings()
    for row in documents:
        values: dict[str, Any] = dict(row)
        object_id = _ensure_object(
            connection,
            object_ids,
            workspace_id=values["workspace_id"],
            storage_key=values["storage_key"],
            media_type=values["media_type"],
            size_bytes=values["size_bytes"],
            sha256=values["sha256"],
            state="available",
            created_at=values["created_at"],
            updated_at=values["created_at"],
        )
        _insert_reference(
            connection,
            workspace_id=values["workspace_id"],
            artifact_object_id=object_id,
            candidate_analysis_id=None,
            document_id=values["id"],
            kind="stored_document",
            lifecycle="persistent",
            expires_at=None,
            created_at=values["created_at"],
        )

    candidates = connection.execute(
        sa.text(
            "SELECT id, workspace_id, document_type, media_type, temporary_storage_key, "
            "converted_pdf_storage_key, preview_storage_key, created_at, updated_at, "
            "expires_at FROM candidate_analyses ORDER BY created_at, id"
        )
    ).mappings()
    for row in candidates:
        values = dict(row)
        source_kind = "source_pdf" if values["document_type"] == "pdf" else "source_docx"
        source_media = values["media_type"] or (
            "application/pdf" if values["document_type"] == "pdf" else DOCX_MEDIA_TYPE
        )
        artifacts = (
            (values["temporary_storage_key"], source_kind, source_media),
            (values["converted_pdf_storage_key"], "converted_pdf", "application/pdf"),
            (values["preview_storage_key"], "preview_png", "image/png"),
        )
        for storage_key, kind, media_type in artifacts:
            if storage_key is None:
                continue
            object_id = _ensure_object(
                connection,
                object_ids,
                workspace_id=values["workspace_id"],
                storage_key=storage_key,
                media_type=media_type,
                size_bytes=None,
                sha256=None,
                state="legacy_pending",
                created_at=values["created_at"],
                updated_at=values["updated_at"],
            )
            _insert_reference(
                connection,
                workspace_id=values["workspace_id"],
                artifact_object_id=object_id,
                candidate_analysis_id=values["id"],
                document_id=None,
                kind=kind,
                lifecycle="temporary",
                expires_at=values["expires_at"],
                created_at=values["created_at"],
            )

    migration_time = datetime.now(UTC)
    connection.execute(
        sa.text(
            "INSERT INTO workspace_storage_usage "
            "(workspace_id, retained_bytes, retained_objects, reconciled_at, updated_at) "
            "SELECT w.id, "
            "COALESCE(SUM(ao.size_bytes) FILTER (WHERE ao.state IN "
            "('available', 'deleting', 'delete_failed')), 0), "
            "COUNT(ao.id) FILTER (WHERE ao.state IN "
            "('available', 'deleting', 'delete_failed')), "
            ":migration_time, :migration_time "
            "FROM workspaces w LEFT JOIN artifact_objects ao ON ao.workspace_id = w.id "
            "GROUP BY w.id"
        ),
        {"migration_time": migration_time},
    )


def upgrade() -> None:
    _create_registry_tables()
    op.add_column(
        "documents",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_documents_workspace_live_created",
        "documents",
        ["workspace_id", "created_at"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    _backfill_registry()

    op.drop_column("documents", "storage_key")
    op.drop_column("candidate_analyses", "temporary_storage_key")
    op.drop_column("candidate_analyses", "converted_pdf_storage_key")
    op.drop_column("candidate_analyses", "preview_storage_key")


def _restore_direct_keys() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "UPDATE documents AS d SET storage_key = restored.storage_key FROM ("
            "SELECT DISTINCT ON (r.document_id) r.document_id, o.storage_key "
            "FROM artifact_references r JOIN artifact_objects o "
            "ON o.id = r.artifact_object_id AND o.workspace_id = r.workspace_id "
            "WHERE r.document_id IS NOT NULL AND r.kind = 'stored_document' "
            "ORDER BY r.document_id, (r.removed_at IS NULL) DESC, r.created_at DESC"
            ") AS restored WHERE d.id = restored.document_id"
        )
    )
    connection.execute(
        sa.text(
            "UPDATE candidate_analyses AS c SET temporary_storage_key = restored.storage_key "
            "FROM (SELECT DISTINCT ON (r.candidate_analysis_id) "
            "r.candidate_analysis_id, o.storage_key FROM artifact_references r "
            "JOIN artifact_objects o ON o.id = r.artifact_object_id "
            "AND o.workspace_id = r.workspace_id WHERE r.candidate_analysis_id IS NOT NULL "
            "AND r.kind IN ('source_pdf', 'source_docx') ORDER BY r.candidate_analysis_id, "
            "(r.removed_at IS NULL) DESC, r.created_at DESC) AS restored "
            "WHERE c.id = restored.candidate_analysis_id"
        )
    )
    connection.execute(
        sa.text(
            "UPDATE candidate_analyses AS c SET converted_pdf_storage_key = "
            "restored.storage_key FROM (SELECT DISTINCT ON (r.candidate_analysis_id) "
            "r.candidate_analysis_id, o.storage_key FROM artifact_references r "
            "JOIN artifact_objects o ON o.id = r.artifact_object_id "
            "AND o.workspace_id = r.workspace_id "
            "WHERE r.candidate_analysis_id IS NOT NULL AND r.kind = 'converted_pdf' "
            "ORDER BY r.candidate_analysis_id, (r.removed_at IS NULL) DESC, "
            "r.created_at DESC) AS restored WHERE c.id = restored.candidate_analysis_id"
        )
    )
    connection.execute(
        sa.text(
            "UPDATE candidate_analyses AS c SET preview_storage_key = restored.storage_key "
            "FROM (SELECT DISTINCT ON (r.candidate_analysis_id) "
            "r.candidate_analysis_id, o.storage_key FROM artifact_references r "
            "JOIN artifact_objects o ON o.id = r.artifact_object_id "
            "AND o.workspace_id = r.workspace_id "
            "WHERE r.candidate_analysis_id IS NOT NULL AND r.kind = 'preview_png' "
            "ORDER BY r.candidate_analysis_id, (r.removed_at IS NULL) DESC, "
            "r.created_at DESC) AS restored WHERE c.id = restored.candidate_analysis_id"
        )
    )


def downgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("storage_key", sa.String(length=512), nullable=True),
    )
    for column_name in (
        "temporary_storage_key",
        "converted_pdf_storage_key",
        "preview_storage_key",
    ):
        op.add_column(
            "candidate_analyses",
            sa.Column(column_name, sa.String(length=512), nullable=True),
        )

    _restore_direct_keys()

    op.alter_column(
        "documents",
        "storage_key",
        existing_type=sa.String(length=512),
        nullable=False,
    )
    op.create_unique_constraint("documents_storage_key_key", "documents", ["storage_key"])
    op.create_unique_constraint(
        "candidate_analyses_temporary_storage_key_key",
        "candidate_analyses",
        ["temporary_storage_key"],
    )
    op.create_unique_constraint(
        "candidate_analyses_converted_pdf_storage_key_key",
        "candidate_analyses",
        ["converted_pdf_storage_key"],
    )
    op.create_unique_constraint(
        "candidate_analyses_preview_storage_key_key",
        "candidate_analyses",
        ["preview_storage_key"],
    )

    op.drop_index("ix_documents_workspace_live_created", table_name="documents")
    op.drop_column("documents", "deleted_at")

    op.drop_table("artifact_references")
    op.drop_table("workspace_storage_usage")
    op.drop_index("ix_artifact_objects_state_created", table_name="artifact_objects")
    op.drop_index("ix_artifact_objects_delete_claim", table_name="artifact_objects")
    op.drop_index("ix_artifact_objects_workspace_state", table_name="artifact_objects")
    op.drop_table("artifact_objects")
