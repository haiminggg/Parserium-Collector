import re
from dataclasses import dataclass, replace
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import RowMapping, and_, exists, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from parserium_collector.adapters.database.tables import (
    artifact_objects,
    artifact_references,
    candidate_analyses,
    discovery_analysis_sessions,
    documents,
    workspace_storage_usage,
)
from parserium_collector.features.storage.errors import (
    ArtifactIntegrityError,
    ArtifactNotFoundError,
)
from parserium_collector.features.storage.keys import validate_storage_key
from parserium_collector.features.storage.models import (
    ArtifactKind,
    ArtifactLifecycle,
    ArtifactObjectState,
    StoredObjectMetadata,
)

RETAINED_STATES = (
    ArtifactObjectState.AVAILABLE.value,
    ArtifactObjectState.DELETING.value,
    ArtifactObjectState.DELETE_FAILED.value,
)
DELETION_STATES = (
    ArtifactObjectState.DELETING.value,
    ArtifactObjectState.DELETE_FAILED.value,
)
SAFE_FAILURE_CODE_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,63}")


def _validate_failure_code(error_code: str) -> str:
    if SAFE_FAILURE_CODE_PATTERN.fullmatch(error_code) is None:
        raise ValueError("The artifact failure code is invalid.")
    return error_code


@dataclass(frozen=True)
class ArtifactObjectRecord:
    id: UUID
    workspace_id: UUID
    storage_key: str
    media_type: str
    size_bytes: int | None
    sha256: str | None
    state: ArtifactObjectState
    available_at: datetime | None
    delete_attempt_count: int
    delete_available_at: datetime | None
    delete_claimed_by: str | None
    delete_lease_expires_at: datetime | None
    failure_code: str | None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


@dataclass(frozen=True)
class ArtifactReferenceRecord:
    id: UUID
    workspace_id: UUID
    artifact_object_id: UUID
    candidate_analysis_id: UUID | None
    document_id: UUID | None
    kind: ArtifactKind
    lifecycle: ArtifactLifecycle
    expires_at: datetime | None
    removed_at: datetime | None
    created_at: datetime


@dataclass(frozen=True)
class WorkspaceStorageUsageRecord:
    workspace_id: UUID
    retained_bytes: int
    retained_objects: int
    reconciled_at: datetime | None
    updated_at: datetime


def _object_record(row: RowMapping) -> ArtifactObjectRecord:
    return ArtifactObjectRecord(
        id=row.id,
        workspace_id=row.workspace_id,
        storage_key=row.storage_key,
        media_type=row.media_type,
        size_bytes=row.size_bytes,
        sha256=row.sha256,
        state=ArtifactObjectState(row.state),
        available_at=row.available_at,
        delete_attempt_count=row.delete_attempt_count,
        delete_available_at=row.delete_available_at,
        delete_claimed_by=row.delete_claimed_by,
        delete_lease_expires_at=row.delete_lease_expires_at,
        failure_code=row.failure_code,
        created_at=row.created_at,
        updated_at=row.updated_at,
        deleted_at=row.deleted_at,
    )


def _reference_record(row: RowMapping) -> ArtifactReferenceRecord:
    return ArtifactReferenceRecord(
        id=row.id,
        workspace_id=row.workspace_id,
        artifact_object_id=row.artifact_object_id,
        candidate_analysis_id=row.candidate_analysis_id,
        document_id=row.document_id,
        kind=ArtifactKind(row.kind),
        lifecycle=ArtifactLifecycle(row.lifecycle),
        expires_at=row.expires_at,
        removed_at=row.removed_at,
        created_at=row.created_at,
    )


def _usage_record(row: RowMapping) -> WorkspaceStorageUsageRecord:
    return WorkspaceStorageUsageRecord(
        workspace_id=row.workspace_id,
        retained_bytes=row.retained_bytes,
        retained_objects=row.retained_objects,
        reconciled_at=row.reconciled_at,
        updated_at=row.updated_at,
    )


def _validate_reference(
    *,
    document_id: UUID | None,
    candidate_analysis_id: UUID | None,
    kind: ArtifactKind,
    lifecycle: ArtifactLifecycle,
    expires_at: datetime | None,
) -> None:
    if (document_id is None) == (candidate_analysis_id is None):
        raise ValueError("An artifact reference must have exactly one owner.")
    if lifecycle is ArtifactLifecycle.TEMPORARY and expires_at is None:
        raise ValueError("A temporary artifact reference requires an expiration time.")
    if lifecycle is ArtifactLifecycle.PERSISTENT and expires_at is not None:
        raise ValueError("A persistent artifact reference cannot expire.")
    if document_id is not None:
        if (
            kind is not ArtifactKind.STORED_DOCUMENT
            or lifecycle is not ArtifactLifecycle.PERSISTENT
        ):
            raise ValueError("A document requires a persistent stored-document artifact.")
    elif kind is ArtifactKind.STORED_DOCUMENT or lifecycle is not ArtifactLifecycle.TEMPORARY:
        raise ValueError("An analysis artifact must be temporary and analysis-scoped.")


class PostgresArtifactRepository:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def _ensure_usage(
        self,
        connection: AsyncConnection,
        workspace_id: UUID,
        now: datetime,
    ) -> None:
        await connection.execute(
            postgresql_insert(workspace_storage_usage)
            .values(
                workspace_id=workspace_id,
                retained_bytes=0,
                retained_objects=0,
                reconciled_at=None,
                updated_at=now,
            )
            .on_conflict_do_nothing(index_elements=[workspace_storage_usage.c.workspace_id])
        )

    async def _adjust_usage(
        self,
        connection: AsyncConnection,
        workspace_id: UUID,
        *,
        bytes_delta: int,
        objects_delta: int,
        now: datetime,
    ) -> None:
        await self._ensure_usage(connection, workspace_id, now)
        await connection.execute(
            update(workspace_storage_usage)
            .where(workspace_storage_usage.c.workspace_id == workspace_id)
            .values(
                retained_bytes=workspace_storage_usage.c.retained_bytes + bytes_delta,
                retained_objects=workspace_storage_usage.c.retained_objects + objects_delta,
                updated_at=now,
            )
        )

    async def begin_upload(
        self,
        workspace_id: UUID,
        storage_key: str,
        object_metadata: StoredObjectMetadata,
        *,
        now: datetime,
    ) -> ArtifactObjectRecord:
        async with self._engine.begin() as connection:
            return await self.begin_upload_in_connection(
                connection,
                workspace_id,
                storage_key,
                object_metadata,
                now=now,
            )

    async def begin_upload_in_connection(
        self,
        connection: AsyncConnection,
        workspace_id: UUID,
        storage_key: str,
        object_metadata: StoredObjectMetadata,
        *,
        now: datetime,
    ) -> ArtifactObjectRecord:
        validated_key = validate_storage_key(storage_key)
        if object_metadata.storage_key != validated_key:
            raise ArtifactIntegrityError("Artifact metadata does not match its storage key.")
        await self._ensure_usage(connection, workspace_id, now)
        result = await connection.execute(
            postgresql_insert(artifact_objects)
            .values(
                id=uuid4(),
                workspace_id=workspace_id,
                storage_key=validated_key,
                media_type=object_metadata.media_type,
                size_bytes=object_metadata.size_bytes,
                sha256=object_metadata.sha256,
                state=ArtifactObjectState.UPLOADING.value,
                available_at=None,
                delete_attempt_count=0,
                delete_available_at=None,
                delete_claimed_by=None,
                delete_lease_expires_at=None,
                failure_code=None,
                created_at=now,
                updated_at=now,
                deleted_at=None,
            )
            .on_conflict_do_nothing(
                index_elements=[artifact_objects.c.workspace_id, artifact_objects.c.storage_key]
            )
            .returning(artifact_objects)
        )
        row = result.mappings().first()
        if row is not None:
            return _object_record(row)

        existing_result = await connection.execute(
            select(artifact_objects)
            .where(
                artifact_objects.c.workspace_id == workspace_id,
                artifact_objects.c.storage_key == validated_key,
            )
            .with_for_update()
        )
        existing = existing_result.mappings().first()
        if existing is None:
            raise ArtifactNotFoundError("The artifact upload could not be registered.")
        if (
            existing.media_type != object_metadata.media_type
            or existing.size_bytes != object_metadata.size_bytes
            or existing.sha256 != object_metadata.sha256
            or existing.state
            not in {ArtifactObjectState.UPLOADING.value, ArtifactObjectState.AVAILABLE.value}
        ):
            raise ArtifactIntegrityError("The storage key is already registered differently.")
        return _object_record(existing)

    async def finalize_reference(
        self,
        artifact_object_id: UUID,
        *,
        workspace_id: UUID,
        document_id: UUID | None,
        candidate_analysis_id: UUID | None,
        kind: ArtifactKind,
        lifecycle: ArtifactLifecycle,
        expires_at: datetime | None,
        now: datetime,
    ) -> ArtifactReferenceRecord:
        async with self._engine.begin() as connection:
            return await self.finalize_reference_in_connection(
                connection,
                artifact_object_id,
                workspace_id=workspace_id,
                document_id=document_id,
                candidate_analysis_id=candidate_analysis_id,
                kind=kind,
                lifecycle=lifecycle,
                expires_at=expires_at,
                now=now,
            )

    async def finalize_reference_in_connection(
        self,
        connection: AsyncConnection,
        artifact_object_id: UUID,
        *,
        workspace_id: UUID,
        document_id: UUID | None,
        candidate_analysis_id: UUID | None,
        kind: ArtifactKind,
        lifecycle: ArtifactLifecycle,
        expires_at: datetime | None,
        now: datetime,
    ) -> ArtifactReferenceRecord:
        _validate_reference(
            document_id=document_id,
            candidate_analysis_id=candidate_analysis_id,
            kind=kind,
            lifecycle=lifecycle,
            expires_at=expires_at,
        )
        object_result = await connection.execute(
            select(artifact_objects)
            .where(
                artifact_objects.c.id == artifact_object_id,
                artifact_objects.c.workspace_id == workspace_id,
            )
            .with_for_update()
        )
        object_row = object_result.mappings().first()
        if object_row is None:
            raise ArtifactNotFoundError("The artifact object was not found.")
        if object_row.state not in {
            ArtifactObjectState.UPLOADING.value,
            ArtifactObjectState.AVAILABLE.value,
        }:
            raise ArtifactIntegrityError("The artifact object cannot accept a reference.")

        if document_id is not None:
            owner_id = await connection.scalar(
                select(documents.c.id).where(
                    documents.c.id == document_id,
                    documents.c.workspace_id == workspace_id,
                    documents.c.deleted_at.is_(None),
                )
            )
            conflict_elements = [artifact_references.c.document_id, artifact_references.c.kind]
            conflict_where = and_(
                artifact_references.c.removed_at.is_(None),
                artifact_references.c.document_id.is_not(None),
            )
        else:
            owner_id = await connection.scalar(
                select(candidate_analyses.c.id).where(
                    candidate_analyses.c.id == candidate_analysis_id,
                    candidate_analyses.c.workspace_id == workspace_id,
                )
            )
            conflict_elements = [
                artifact_references.c.candidate_analysis_id,
                artifact_references.c.kind,
            ]
            conflict_where = and_(
                artifact_references.c.removed_at.is_(None),
                artifact_references.c.candidate_analysis_id.is_not(None),
            )
        if owner_id is None:
            raise ArtifactNotFoundError("The artifact owner was not found.")

        reference_id = uuid4()
        insert_result = await connection.execute(
            postgresql_insert(artifact_references)
            .values(
                id=reference_id,
                workspace_id=workspace_id,
                artifact_object_id=artifact_object_id,
                candidate_analysis_id=candidate_analysis_id,
                document_id=document_id,
                kind=kind.value,
                lifecycle=lifecycle.value,
                expires_at=expires_at,
                removed_at=None,
                created_at=now,
            )
            .on_conflict_do_nothing(
                index_elements=conflict_elements,
                index_where=conflict_where,
            )
            .returning(artifact_references)
        )
        reference_row = insert_result.mappings().first()
        if reference_row is None:
            existing_result = await connection.execute(
                select(artifact_references).where(
                    artifact_references.c.workspace_id == workspace_id,
                    artifact_references.c.document_id == document_id,
                    artifact_references.c.candidate_analysis_id == candidate_analysis_id,
                    artifact_references.c.kind == kind.value,
                    artifact_references.c.removed_at.is_(None),
                )
            )
            reference_row = existing_result.mappings().one()

        if reference_row.artifact_object_id != artifact_object_id:
            await connection.execute(
                update(artifact_objects)
                .where(
                    artifact_objects.c.id == artifact_object_id,
                    artifact_objects.c.workspace_id == workspace_id,
                )
                .values(
                    state=ArtifactObjectState.DELETING.value,
                    delete_available_at=now,
                    updated_at=now,
                )
            )
            return _reference_record(reference_row)

        if object_row.state == ArtifactObjectState.UPLOADING.value:
            await connection.execute(
                update(artifact_objects)
                .where(
                    artifact_objects.c.id == artifact_object_id,
                    artifact_objects.c.workspace_id == workspace_id,
                )
                .values(
                    state=ArtifactObjectState.AVAILABLE.value,
                    available_at=now,
                    updated_at=now,
                    failure_code=None,
                )
            )
            await self._adjust_usage(
                connection,
                workspace_id,
                bytes_delta=object_row.size_bytes,
                objects_delta=1,
                now=now,
            )
        return _reference_record(reference_row)

    async def discard_upload(
        self,
        artifact_object_id: UUID,
        workspace_id: UUID,
        *,
        now: datetime,
    ) -> ArtifactObjectRecord | None:
        async with self._engine.begin() as connection:
            return await self.discard_upload_in_connection(
                connection,
                artifact_object_id,
                workspace_id,
                now=now,
            )

    async def discard_upload_in_connection(
        self,
        connection: AsyncConnection,
        artifact_object_id: UUID,
        workspace_id: UUID,
        *,
        now: datetime,
    ) -> ArtifactObjectRecord | None:
        object_result = await connection.execute(
            select(artifact_objects)
            .where(
                artifact_objects.c.id == artifact_object_id,
                artifact_objects.c.workspace_id == workspace_id,
            )
            .with_for_update()
        )
        row = object_result.mappings().first()
        if row is None:
            return None
        live_references = await self._count_live_references(connection, artifact_object_id)
        if live_references or row.state not in {
            ArtifactObjectState.UPLOADING.value,
            ArtifactObjectState.DELETING.value,
        }:
            return None
        if row.state == ArtifactObjectState.DELETING.value:
            return _object_record(row)
        updated = await connection.execute(
            update(artifact_objects)
            .where(artifact_objects.c.id == artifact_object_id)
            .values(
                state=ArtifactObjectState.DELETING.value,
                delete_available_at=now,
                updated_at=now,
            )
            .returning(artifact_objects)
        )
        return _object_record(updated.mappings().one())

    async def resolve_document_object(
        self,
        workspace_id: UUID,
        document_id: UUID,
    ) -> ArtifactObjectRecord | None:
        async with self._engine.connect() as connection:
            result = await connection.execute(
                select(artifact_objects)
                .join(
                    artifact_references,
                    and_(
                        artifact_references.c.artifact_object_id == artifact_objects.c.id,
                        artifact_references.c.workspace_id == artifact_objects.c.workspace_id,
                    ),
                )
                .join(
                    documents,
                    and_(
                        documents.c.id == artifact_references.c.document_id,
                        documents.c.workspace_id == artifact_references.c.workspace_id,
                    ),
                )
                .where(
                    artifact_references.c.workspace_id == workspace_id,
                    artifact_references.c.document_id == document_id,
                    artifact_references.c.kind == ArtifactKind.STORED_DOCUMENT.value,
                    artifact_references.c.removed_at.is_(None),
                    documents.c.deleted_at.is_(None),
                    artifact_objects.c.state.in_(
                        (
                            ArtifactObjectState.AVAILABLE.value,
                            ArtifactObjectState.LEGACY_PENDING.value,
                        )
                    ),
                )
            )
            row = result.mappings().first()
            return None if row is None else _object_record(row)

    async def resolve_analysis_object(
        self,
        workspace_id: UUID,
        candidate_analysis_id: UUID,
        kind: ArtifactKind,
    ) -> ArtifactObjectRecord | None:
        if kind is ArtifactKind.STORED_DOCUMENT:
            raise ValueError("A stored document is not an analysis artifact.")
        async with self._engine.connect() as connection:
            result = await connection.execute(
                select(artifact_objects)
                .join(
                    artifact_references,
                    and_(
                        artifact_references.c.artifact_object_id == artifact_objects.c.id,
                        artifact_references.c.workspace_id == artifact_objects.c.workspace_id,
                    ),
                )
                .where(
                    artifact_references.c.workspace_id == workspace_id,
                    artifact_references.c.candidate_analysis_id == candidate_analysis_id,
                    artifact_references.c.kind == kind.value,
                    artifact_references.c.removed_at.is_(None),
                    artifact_objects.c.state.in_(
                        (
                            ArtifactObjectState.AVAILABLE.value,
                            ArtifactObjectState.LEGACY_PENDING.value,
                        )
                    ),
                )
            )
            row = result.mappings().first()
            return None if row is None else _object_record(row)

    async def _count_live_references(
        self,
        connection: AsyncConnection,
        artifact_object_id: UUID,
    ) -> int:
        count = await connection.scalar(
            select(func.count())
            .select_from(artifact_references)
            .where(
                artifact_references.c.artifact_object_id == artifact_object_id,
                artifact_references.c.removed_at.is_(None),
            )
        )
        return int(count or 0)

    async def _mark_unreferenced_for_deletion(
        self,
        connection: AsyncConnection,
        artifact_object_id: UUID,
        *,
        now: datetime,
    ) -> ArtifactObjectRecord | None:
        object_result = await connection.execute(
            select(artifact_objects)
            .where(artifact_objects.c.id == artifact_object_id)
            .with_for_update()
        )
        row = object_result.mappings().first()
        if row is None or await self._count_live_references(connection, artifact_object_id):
            return None if row is None else _object_record(row)
        if row.state in {
            ArtifactObjectState.UPLOADING.value,
            ArtifactObjectState.AVAILABLE.value,
        }:
            result = await connection.execute(
                update(artifact_objects)
                .where(artifact_objects.c.id == artifact_object_id)
                .values(
                    state=ArtifactObjectState.DELETING.value,
                    delete_available_at=now,
                    updated_at=now,
                )
                .returning(artifact_objects)
            )
            return _object_record(result.mappings().one())
        if row.state == ArtifactObjectState.LEGACY_PENDING.value:
            result = await connection.execute(
                update(artifact_objects)
                .where(artifact_objects.c.id == artifact_object_id)
                .values(delete_available_at=now, updated_at=now)
                .returning(artifact_objects)
            )
            return _object_record(result.mappings().one())
        return _object_record(row)

    async def remove_document_reference(
        self,
        workspace_id: UUID,
        document_id: UUID,
        *,
        now: datetime,
    ) -> ArtifactObjectRecord | None:
        async with self._engine.begin() as connection:
            return await self.remove_document_reference_in_connection(
                connection,
                workspace_id,
                document_id,
                now=now,
            )

    async def remove_document_reference_in_connection(
        self,
        connection: AsyncConnection,
        workspace_id: UUID,
        document_id: UUID,
        *,
        now: datetime,
    ) -> ArtifactObjectRecord | None:
        reference_result = await connection.execute(
            select(artifact_references.c.artifact_object_id).where(
                artifact_references.c.workspace_id == workspace_id,
                artifact_references.c.document_id == document_id,
                artifact_references.c.kind == ArtifactKind.STORED_DOCUMENT.value,
                artifact_references.c.removed_at.is_(None),
            )
        )
        artifact_object_id = reference_result.scalar_one_or_none()
        if artifact_object_id is None:
            return None
        await connection.execute(
            select(artifact_objects.c.id)
            .where(
                artifact_objects.c.id == artifact_object_id,
                artifact_objects.c.workspace_id == workspace_id,
            )
            .with_for_update()
        )
        removed = await connection.execute(
            update(artifact_references)
            .where(
                artifact_references.c.workspace_id == workspace_id,
                artifact_references.c.document_id == document_id,
                artifact_references.c.kind == ArtifactKind.STORED_DOCUMENT.value,
                artifact_references.c.removed_at.is_(None),
            )
            .values(removed_at=now)
        )
        if removed.rowcount == 0:
            return None
        return await self._mark_unreferenced_for_deletion(
            connection,
            artifact_object_id,
            now=now,
        )

    async def claim_expired_references(
        self,
        *,
        now: datetime,
        limit: int,
    ) -> tuple[ArtifactReferenceRecord, ...]:
        if limit <= 0:
            return ()
        active_analysis_reference = exists(
            select(candidate_analyses.c.id)
            .join(
                discovery_analysis_sessions,
                discovery_analysis_sessions.c.id == candidate_analyses.c.session_id,
            )
            .where(
                candidate_analyses.c.id
                == artifact_references.c.candidate_analysis_id,
                discovery_analysis_sessions.c.status.in_(("queued", "running")),
            )
        )
        async with self._engine.begin() as connection:
            result = await connection.execute(
                select(artifact_references)
                .where(
                    artifact_references.c.lifecycle == ArtifactLifecycle.TEMPORARY.value,
                    artifact_references.c.removed_at.is_(None),
                    artifact_references.c.expires_at <= now,
                    ~active_analysis_reference,
                )
                .order_by(artifact_references.c.expires_at, artifact_references.c.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
            rows = tuple(result.mappings())
            if not rows:
                return ()
            reference_ids = [row.id for row in rows]
            await connection.execute(
                update(artifact_references)
                .where(artifact_references.c.id.in_(reference_ids))
                .values(removed_at=now)
            )
            for object_id in sorted({row.artifact_object_id for row in rows}, key=str):
                await self._mark_unreferenced_for_deletion(connection, object_id, now=now)
            return tuple(replace(_reference_record(row), removed_at=now) for row in rows)

    async def claim_orphan_objects(
        self,
        *,
        now: datetime,
        orphaned_before: datetime,
        limit: int,
    ) -> tuple[ArtifactObjectRecord, ...]:
        if limit <= 0:
            return ()
        live_reference = exists(
            select(artifact_references.c.id).where(
                artifact_references.c.artifact_object_id == artifact_objects.c.id,
                artifact_references.c.removed_at.is_(None),
            )
        )
        async with self._engine.begin() as connection:
            result = await connection.execute(
                select(artifact_objects.c.id)
                .where(
                    artifact_objects.c.state.in_(
                        (
                            ArtifactObjectState.UPLOADING.value,
                            ArtifactObjectState.AVAILABLE.value,
                            ArtifactObjectState.LEGACY_PENDING.value,
                        )
                    ),
                    artifact_objects.c.created_at <= orphaned_before,
                    ~live_reference,
                )
                .order_by(artifact_objects.c.created_at, artifact_objects.c.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
            object_ids = tuple(result.scalars())
            claims: list[ArtifactObjectRecord] = []
            for object_id in object_ids:
                claimed = await self._mark_unreferenced_for_deletion(
                    connection,
                    object_id,
                    now=now,
                )
                if claimed is not None:
                    claims.append(claimed)
            return tuple(claims)

    async def claim_deletions(
        self,
        worker_id: str,
        *,
        now: datetime,
        lease_expires_at: datetime,
        limit: int,
    ) -> tuple[ArtifactObjectRecord, ...]:
        if not worker_id or limit <= 0 or lease_expires_at <= now:
            return ()
        async with self._engine.begin() as connection:
            result = await connection.execute(
                select(artifact_objects.c.id)
                .where(
                    or_(
                        artifact_objects.c.state.in_(DELETION_STATES),
                        and_(
                            artifact_objects.c.state == ArtifactObjectState.LEGACY_PENDING.value,
                            artifact_objects.c.delete_available_at.is_not(None),
                        ),
                    ),
                    artifact_objects.c.delete_available_at <= now,
                    or_(
                        artifact_objects.c.delete_lease_expires_at.is_(None),
                        artifact_objects.c.delete_lease_expires_at <= now,
                    ),
                )
                .order_by(artifact_objects.c.delete_available_at, artifact_objects.c.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
            object_ids = tuple(result.scalars())
            if not object_ids:
                return ()
            claimed = await connection.execute(
                update(artifact_objects)
                .where(artifact_objects.c.id.in_(object_ids))
                .values(
                    delete_claimed_by=worker_id,
                    delete_lease_expires_at=lease_expires_at,
                    updated_at=now,
                )
                .returning(artifact_objects)
            )
            rows_by_id = {row.id: row for row in claimed.mappings()}
            return tuple(_object_record(rows_by_id[object_id]) for object_id in object_ids)

    async def complete_deletion(
        self,
        artifact_object_id: UUID,
        workspace_id: UUID,
        worker_id: str,
        *,
        now: datetime,
    ) -> bool:
        async with self._engine.begin() as connection:
            result = await connection.execute(
                select(artifact_objects)
                .where(
                    artifact_objects.c.id == artifact_object_id,
                    artifact_objects.c.workspace_id == workspace_id,
                    artifact_objects.c.delete_claimed_by == worker_id,
                )
                .with_for_update()
            )
            row = result.mappings().first()
            if row is None:
                return False
            if row.state == ArtifactObjectState.LEGACY_PENDING.value:
                await connection.execute(
                    artifact_objects.delete().where(artifact_objects.c.id == artifact_object_id)
                )
                return True
            if row.state not in DELETION_STATES:
                return False
            await connection.execute(
                update(artifact_objects)
                .where(artifact_objects.c.id == artifact_object_id)
                .values(
                    state=ArtifactObjectState.DELETED.value,
                    deleted_at=now,
                    delete_claimed_by=None,
                    delete_lease_expires_at=None,
                    failure_code=None,
                    updated_at=now,
                )
            )
            if row.available_at is not None:
                await self._adjust_usage(
                    connection,
                    workspace_id,
                    bytes_delta=-row.size_bytes,
                    objects_delta=-1,
                    now=now,
                )
            return True

    async def fail_deletion(
        self,
        artifact_object_id: UUID,
        workspace_id: UUID,
        worker_id: str,
        *,
        error_code: str,
        retry_at: datetime,
        now: datetime,
    ) -> bool:
        validated_error_code = _validate_failure_code(error_code)
        async with self._engine.begin() as connection:
            result = await connection.execute(
                select(artifact_objects)
                .where(
                    artifact_objects.c.id == artifact_object_id,
                    artifact_objects.c.workspace_id == workspace_id,
                    artifact_objects.c.delete_claimed_by == worker_id,
                )
                .with_for_update()
            )
            row = result.mappings().first()
            if row is None or row.state not in {
                *DELETION_STATES,
                ArtifactObjectState.LEGACY_PENDING.value,
            }:
                return False
            values: dict[str, object] = {
                "delete_attempt_count": row.delete_attempt_count + 1,
                "delete_available_at": retry_at,
                "delete_claimed_by": None,
                "delete_lease_expires_at": None,
                "failure_code": validated_error_code,
                "updated_at": now,
            }
            if row.state != ArtifactObjectState.LEGACY_PENDING.value:
                values["state"] = ArtifactObjectState.DELETE_FAILED.value
            await connection.execute(
                update(artifact_objects)
                .where(artifact_objects.c.id == artifact_object_id)
                .values(**values)
            )
            return True

    async def fail_legacy_metadata(
        self,
        artifact_object_id: UUID,
        workspace_id: UUID,
        worker_id: str,
        *,
        error_code: str,
        retry_at: datetime,
        now: datetime,
    ) -> bool:
        validated_error_code = _validate_failure_code(error_code)
        if retry_at <= now:
            raise ValueError("The legacy metadata retry must be scheduled in the future.")
        async with self._engine.begin() as connection:
            result = await connection.execute(
                select(artifact_objects)
                .where(
                    artifact_objects.c.id == artifact_object_id,
                    artifact_objects.c.workspace_id == workspace_id,
                    artifact_objects.c.state == ArtifactObjectState.LEGACY_PENDING.value,
                    artifact_objects.c.delete_claimed_by == worker_id,
                    artifact_objects.c.delete_available_at.is_(None),
                )
                .with_for_update()
            )
            row = result.mappings().first()
            if row is None:
                return False
            await connection.execute(
                update(artifact_objects)
                .where(artifact_objects.c.id == artifact_object_id)
                .values(
                    delete_attempt_count=row.delete_attempt_count + 1,
                    delete_claimed_by=None,
                    delete_lease_expires_at=retry_at,
                    failure_code=validated_error_code,
                    updated_at=now,
                )
            )
            return True

    async def claim_legacy_pending(
        self,
        worker_id: str,
        *,
        now: datetime,
        lease_expires_at: datetime,
        limit: int,
    ) -> tuple[ArtifactObjectRecord, ...]:
        if not worker_id or limit <= 0 or lease_expires_at <= now:
            return ()
        live_reference = exists(
            select(artifact_references.c.id).where(
                artifact_references.c.artifact_object_id == artifact_objects.c.id,
                artifact_references.c.removed_at.is_(None),
            )
        )
        async with self._engine.begin() as connection:
            result = await connection.execute(
                select(artifact_objects.c.id)
                .where(
                    artifact_objects.c.state == ArtifactObjectState.LEGACY_PENDING.value,
                    artifact_objects.c.delete_available_at.is_(None),
                    or_(
                        artifact_objects.c.delete_lease_expires_at.is_(None),
                        artifact_objects.c.delete_lease_expires_at <= now,
                    ),
                    live_reference,
                )
                .order_by(artifact_objects.c.created_at, artifact_objects.c.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
            object_ids = tuple(result.scalars())
            if not object_ids:
                return ()
            claimed = await connection.execute(
                update(artifact_objects)
                .where(artifact_objects.c.id.in_(object_ids))
                .values(
                    delete_claimed_by=worker_id,
                    delete_lease_expires_at=lease_expires_at,
                    updated_at=now,
                )
                .returning(artifact_objects)
            )
            rows_by_id = {row.id: row for row in claimed.mappings()}
            return tuple(_object_record(rows_by_id[object_id]) for object_id in object_ids)

    async def complete_legacy_metadata(
        self,
        artifact_object_id: UUID,
        workspace_id: UUID,
        worker_id: str,
        object_metadata: StoredObjectMetadata,
        *,
        now: datetime,
    ) -> bool:
        async with self._engine.begin() as connection:
            result = await connection.execute(
                select(artifact_objects)
                .where(
                    artifact_objects.c.id == artifact_object_id,
                    artifact_objects.c.workspace_id == workspace_id,
                    artifact_objects.c.state == ArtifactObjectState.LEGACY_PENDING.value,
                    artifact_objects.c.delete_claimed_by == worker_id,
                    artifact_objects.c.delete_available_at.is_(None),
                )
                .with_for_update()
            )
            row = result.mappings().first()
            if row is None:
                return False
            if row.storage_key != object_metadata.storage_key:
                raise ArtifactIntegrityError("Measured metadata has a different storage key.")
            await connection.execute(
                update(artifact_objects)
                .where(artifact_objects.c.id == artifact_object_id)
                .values(
                    media_type=object_metadata.media_type,
                    size_bytes=object_metadata.size_bytes,
                    sha256=object_metadata.sha256,
                    state=ArtifactObjectState.AVAILABLE.value,
                    available_at=now,
                    delete_claimed_by=None,
                    delete_lease_expires_at=None,
                    failure_code=None,
                    updated_at=now,
                )
            )
            await self._adjust_usage(
                connection,
                workspace_id,
                bytes_delta=object_metadata.size_bytes,
                objects_delta=1,
                now=now,
            )
            return True

    async def get_usage(self, workspace_id: UUID) -> WorkspaceStorageUsageRecord:
        async with self._engine.begin() as connection:
            now = await connection.scalar(select(func.now()))
            if now is None:
                raise RuntimeError("The database did not provide a current time.")
            await self._ensure_usage(connection, workspace_id, now)
            result = await connection.execute(
                select(workspace_storage_usage).where(
                    workspace_storage_usage.c.workspace_id == workspace_id
                )
            )
            return _usage_record(result.mappings().one())

    async def list_usage_reconciliation_candidates(self, *, limit: int) -> tuple[UUID, ...]:
        if limit <= 0:
            return ()
        async with self._engine.connect() as connection:
            result = await connection.execute(
                select(workspace_storage_usage.c.workspace_id)
                .order_by(
                    workspace_storage_usage.c.reconciled_at.asc().nulls_first(),
                    workspace_storage_usage.c.updated_at,
                    workspace_storage_usage.c.workspace_id,
                )
                .limit(limit)
            )
            return tuple(result.scalars())

    async def reconcile_usage(
        self,
        workspace_id: UUID,
        *,
        now: datetime,
    ) -> WorkspaceStorageUsageRecord:
        async with self._engine.begin() as connection:
            await self._ensure_usage(connection, workspace_id, now)
            await connection.execute(
                select(workspace_storage_usage.c.workspace_id)
                .where(workspace_storage_usage.c.workspace_id == workspace_id)
                .with_for_update()
            )
            totals = (
                (
                    await connection.execute(
                        select(
                            func.coalesce(func.sum(artifact_objects.c.size_bytes), 0).label(
                                "retained_bytes"
                            ),
                            func.count(artifact_objects.c.id).label("retained_objects"),
                        ).where(
                            artifact_objects.c.workspace_id == workspace_id,
                            artifact_objects.c.state.in_(RETAINED_STATES),
                            artifact_objects.c.available_at.is_not(None),
                        )
                    )
                )
                .mappings()
                .one()
            )
            result = await connection.execute(
                update(workspace_storage_usage)
                .where(workspace_storage_usage.c.workspace_id == workspace_id)
                .values(
                    retained_bytes=totals.retained_bytes,
                    retained_objects=totals.retained_objects,
                    reconciled_at=now,
                    updated_at=now,
                )
                .returning(workspace_storage_usage)
            )
            return _usage_record(result.mappings().one())

    async def count_live_legacy_pending(self, workspace_id: UUID | None = None) -> int:
        live_reference = exists(
            select(artifact_references.c.id).where(
                artifact_references.c.artifact_object_id == artifact_objects.c.id,
                artifact_references.c.removed_at.is_(None),
            )
        )
        conditions = [
            artifact_objects.c.state == ArtifactObjectState.LEGACY_PENDING.value,
            live_reference,
        ]
        if workspace_id is not None:
            conditions.append(artifact_objects.c.workspace_id == workspace_id)
        async with self._engine.connect() as connection:
            count = await connection.scalar(
                select(func.count()).select_from(artifact_objects).where(*conditions)
            )
            return int(count or 0)
