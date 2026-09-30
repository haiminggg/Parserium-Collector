from collections.abc import Sequence
from datetime import datetime
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy import RowMapping, and_, delete, func, insert, or_, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from parserium_collector.adapters.database.tables import (
    collection_jobs,
    document_exports,
    documents,
)
from parserium_collector.features.acquisition.models import (
    CollectionCandidate,
    CollectionJobRecord,
    CollectionJobStatus,
    DocumentExportRecord,
    DocumentType,
    ExportStatus,
    StoredDocumentRecord,
)
from parserium_collector.features.acquisition.pagination import Page, PageCursor
from parserium_collector.features.storage.models import ArtifactKind, ArtifactLifecycle
from parserium_collector.features.storage.repository import PostgresArtifactRepository


def _collection_job_record(row: RowMapping) -> CollectionJobRecord:
    return CollectionJobRecord(
        id=row.id,
        workspace_id=row.workspace_id,
        source_url=row.source_url,
        title=row.title,
        expected_document_type=DocumentType(row.expected_document_type),
        status=CollectionJobStatus(row.status),
        attempt_count=row.attempt_count,
        available_at=row.available_at,
        claimed_by=row.claimed_by,
        lease_expires_at=row.lease_expires_at,
        bytes_downloaded=row.bytes_downloaded,
        content_length=row.content_length,
        document_id=row.document_id,
        error_code=row.error_code,
        error_detail=row.error_detail,
        error_retryable=row.error_retryable,
        created_at=row.created_at,
        updated_at=row.updated_at,
        started_at=row.started_at,
        completed_at=row.completed_at,
    )


def _stored_document_record(row: RowMapping) -> StoredDocumentRecord:
    return StoredDocumentRecord(
        id=row.id,
        workspace_id=row.workspace_id,
        sha256=row.sha256,
        document_type=DocumentType(row.document_type),
        media_type=row.media_type,
        size_bytes=row.size_bytes,
        safe_filename=row.safe_filename,
        created_at=row.created_at,
    )


def _export_record(row: RowMapping) -> DocumentExportRecord:
    return DocumentExportRecord(
        id=row.id,
        workspace_id=row.workspace_id,
        document_id=row.document_id,
        relative_directory=row.relative_directory,
        target_filename=row.target_filename,
        exported_relative_path=row.exported_relative_path,
        status=ExportStatus(row.status),
        attempt_count=row.attempt_count,
        available_at=row.available_at,
        claimed_by=row.claimed_by,
        lease_expires_at=row.lease_expires_at,
        error_code=row.error_code,
        error_detail=row.error_detail,
        created_at=row.created_at,
        updated_at=row.updated_at,
        completed_at=row.completed_at,
    )


class AcquisitionRepository(Protocol):
    async def create_collection_jobs(
        self,
        workspace_id: UUID,
        candidates: Sequence[CollectionCandidate],
        now: datetime,
        created_by_user_id: UUID | None = None,
    ) -> tuple[CollectionJobRecord, ...]: ...

    async def get_collection_job(
        self,
        workspace_id: UUID,
        job_id: UUID,
    ) -> CollectionJobRecord | None: ...

    async def list_collection_jobs(
        self,
        workspace_id: UUID,
        limit: int,
    ) -> tuple[CollectionJobRecord, ...]: ...

    async def list_collection_jobs_page(
        self,
        workspace_id: UUID,
        limit: int,
        cursor: PageCursor | None,
    ) -> Page[CollectionJobRecord]: ...

    async def delete_completed_collection_jobs(self, workspace_id: UUID) -> int: ...

    async def claim_collection_job(
        self,
        worker_id: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> CollectionJobRecord | None: ...

    async def renew_collection_lease(
        self,
        job_id: UUID,
        worker_id: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> bool: ...

    async def update_collection_progress(
        self,
        job_id: UUID,
        worker_id: str,
        bytes_downloaded: int,
        content_length: int | None,
        now: datetime,
    ) -> None: ...

    async def mark_collection_validating(
        self,
        job_id: UUID,
        worker_id: str,
        now: datetime,
    ) -> None: ...

    async def complete_collection(
        self,
        job_id: UUID,
        worker_id: str,
        *,
        sha256: str,
        document_type: DocumentType,
        media_type: str,
        size_bytes: int,
        document_id: UUID,
        artifact_object_id: UUID,
        safe_filename: str,
        now: datetime,
    ) -> CollectionJobRecord: ...

    async def fail_collection(
        self,
        job_id: UUID,
        worker_id: str,
        *,
        error_code: str,
        error_detail: str,
        retryable: bool,
        retry_at: datetime | None,
        now: datetime,
    ) -> None: ...

    async def retry_collection_job(
        self,
        workspace_id: UUID,
        job_id: UUID,
        now: datetime,
    ) -> bool: ...

    async def get_document(
        self,
        workspace_id: UUID,
        document_id: UUID,
    ) -> StoredDocumentRecord | None: ...

    async def delete_document(
        self,
        workspace_id: UUID,
        document_id: UUID,
        now: datetime,
    ) -> bool: ...

    async def list_documents(
        self,
        workspace_id: UUID,
        limit: int,
    ) -> tuple[StoredDocumentRecord, ...]: ...

    async def list_documents_page(
        self,
        workspace_id: UUID,
        limit: int,
        cursor: PageCursor | None,
    ) -> Page[StoredDocumentRecord]: ...

    async def create_export(
        self,
        workspace_id: UUID,
        document_id: UUID,
        relative_directory: str,
        target_filename: str,
        now: datetime,
        created_by_user_id: UUID | None = None,
    ) -> DocumentExportRecord: ...

    async def claim_export(
        self,
        worker_id: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> DocumentExportRecord | None: ...

    async def renew_export_lease(
        self,
        export_id: UUID,
        worker_id: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> bool: ...

    async def complete_export(
        self,
        export_id: UUID,
        worker_id: str,
        exported_relative_path: str,
        now: datetime,
    ) -> DocumentExportRecord: ...

    async def fail_export(
        self,
        export_id: UUID,
        worker_id: str,
        *,
        error_code: str,
        error_detail: str,
        retry_at: datetime | None,
        now: datetime,
    ) -> None: ...

    async def list_exports(
        self,
        workspace_id: UUID,
        limit: int,
    ) -> tuple[DocumentExportRecord, ...]: ...


class PostgresAcquisitionRepository:
    def __init__(
        self,
        engine: AsyncEngine,
        artifact_repository: PostgresArtifactRepository | None = None,
    ) -> None:
        self._engine = engine
        self._artifact_repository = artifact_repository or PostgresArtifactRepository(engine)

    async def create_collection_jobs(
        self,
        workspace_id: UUID,
        candidates: Sequence[CollectionCandidate],
        now: datetime,
        created_by_user_id: UUID | None = None,
    ) -> tuple[CollectionJobRecord, ...]:
        values = [
            {
                "id": uuid4(),
                "workspace_id": workspace_id,
                "created_by_user_id": created_by_user_id,
                "source_url": str(candidate.url),
                "title": candidate.title,
                "expected_document_type": candidate.document_type.value,
                "status": CollectionJobStatus.QUEUED.value,
                "attempt_count": 0,
                "available_at": now,
                "claimed_by": None,
                "lease_expires_at": None,
                "bytes_downloaded": 0,
                "content_length": None,
                "document_id": None,
                "error_code": None,
                "error_detail": None,
                "error_retryable": None,
                "created_at": now,
                "updated_at": now,
                "started_at": None,
                "completed_at": None,
            }
            for candidate in candidates
        ]
        statement = insert(collection_jobs).values(values).returning(*collection_jobs.c)
        async with self._engine.begin() as connection:
            rows = (await connection.execute(statement)).mappings().all()
        return tuple(_collection_job_record(row) for row in rows)

    async def get_collection_job(
        self,
        workspace_id: UUID,
        job_id: UUID,
    ) -> CollectionJobRecord | None:
        statement = select(collection_jobs).where(
            collection_jobs.c.workspace_id == workspace_id,
            collection_jobs.c.id == job_id,
        )
        async with self._engine.connect() as connection:
            row = (await connection.execute(statement)).mappings().one_or_none()
        return None if row is None else _collection_job_record(row)

    async def list_collection_jobs(
        self,
        workspace_id: UUID,
        limit: int,
    ) -> tuple[CollectionJobRecord, ...]:
        statement = (
            select(collection_jobs)
            .where(collection_jobs.c.workspace_id == workspace_id)
            .order_by(collection_jobs.c.created_at.desc(), collection_jobs.c.id.desc())
            .limit(limit)
        )
        async with self._engine.connect() as connection:
            rows = (await connection.execute(statement)).mappings().all()
        return tuple(_collection_job_record(row) for row in rows)

    async def list_collection_jobs_page(
        self,
        workspace_id: UUID,
        limit: int,
        cursor: PageCursor | None,
    ) -> Page[CollectionJobRecord]:
        statement = select(collection_jobs).where(collection_jobs.c.workspace_id == workspace_id)
        if cursor is not None:
            statement = statement.where(
                or_(
                    collection_jobs.c.created_at < cursor.created_at,
                    and_(
                        collection_jobs.c.created_at == cursor.created_at,
                        collection_jobs.c.id < cursor.id,
                    ),
                )
            )
        statement = statement.order_by(
            collection_jobs.c.created_at.desc(),
            collection_jobs.c.id.desc(),
        ).limit(limit + 1)
        async with self._engine.connect() as connection:
            total = int(
                (
                    await connection.execute(
                        select(func.count())
                        .select_from(collection_jobs)
                        .where(collection_jobs.c.workspace_id == workspace_id)
                    )
                ).scalar_one()
            )
            rows = (await connection.execute(statement)).mappings().all()
        page_rows = rows[:limit]
        records = tuple(_collection_job_record(row) for row in page_rows)
        next_cursor = None
        if len(rows) > limit and records:
            last = records[-1]
            next_cursor = PageCursor(created_at=last.created_at, id=last.id)
        return Page(items=records, total=total, next_cursor=next_cursor)

    async def delete_completed_collection_jobs(self, workspace_id: UUID) -> int:
        statement = (
            delete(collection_jobs)
            .where(
                collection_jobs.c.workspace_id == workspace_id,
                collection_jobs.c.status.in_(
                    (
                        CollectionJobStatus.COMPLETED.value,
                        CollectionJobStatus.DUPLICATE.value,
                    )
                ),
            )
            .returning(collection_jobs.c.id)
        )
        async with self._engine.begin() as connection:
            deleted_ids = (await connection.execute(statement)).scalars().all()
        return len(deleted_ids)

    async def claim_collection_job(
        self,
        worker_id: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> CollectionJobRecord | None:
        eligible = or_(
            and_(
                collection_jobs.c.status == CollectionJobStatus.QUEUED.value,
                collection_jobs.c.available_at <= now,
            ),
            and_(
                collection_jobs.c.status.in_(
                    (
                        CollectionJobStatus.DOWNLOADING.value,
                        CollectionJobStatus.VALIDATING.value,
                    )
                ),
                collection_jobs.c.lease_expires_at < now,
            ),
        )
        claimable = (
            select(collection_jobs.c.id)
            .where(eligible)
            .order_by(collection_jobs.c.available_at, collection_jobs.c.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        async with self._engine.begin() as connection:
            job_id = (await connection.execute(claimable)).scalar_one_or_none()
            if job_id is None:
                return None
            statement = (
                update(collection_jobs)
                .where(collection_jobs.c.id == job_id)
                .values(
                    status=CollectionJobStatus.DOWNLOADING.value,
                    attempt_count=collection_jobs.c.attempt_count + 1,
                    claimed_by=worker_id,
                    lease_expires_at=lease_expires_at,
                    error_code=None,
                    error_detail=None,
                    error_retryable=None,
                    updated_at=now,
                    started_at=now,
                    completed_at=None,
                )
                .returning(*collection_jobs.c)
            )
            row = (await connection.execute(statement)).mappings().one()
        return _collection_job_record(row)

    async def update_collection_progress(
        self,
        job_id: UUID,
        worker_id: str,
        bytes_downloaded: int,
        content_length: int | None,
        now: datetime,
    ) -> None:
        statement = (
            update(collection_jobs)
            .where(
                collection_jobs.c.id == job_id,
                collection_jobs.c.claimed_by == worker_id,
                collection_jobs.c.status == CollectionJobStatus.DOWNLOADING.value,
            )
            .values(
                bytes_downloaded=bytes_downloaded,
                content_length=content_length,
                updated_at=now,
            )
        )
        async with self._engine.begin() as connection:
            await connection.execute(statement)

    async def renew_collection_lease(
        self,
        job_id: UUID,
        worker_id: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> bool:
        statement = (
            update(collection_jobs)
            .where(
                collection_jobs.c.id == job_id,
                collection_jobs.c.claimed_by == worker_id,
                collection_jobs.c.status.in_(
                    (
                        CollectionJobStatus.DOWNLOADING.value,
                        CollectionJobStatus.VALIDATING.value,
                    )
                ),
                collection_jobs.c.lease_expires_at >= now,
            )
            .values(lease_expires_at=lease_expires_at, updated_at=now)
            .returning(collection_jobs.c.id)
        )
        async with self._engine.begin() as connection:
            return (await connection.execute(statement)).scalar_one_or_none() is not None

    async def mark_collection_validating(
        self,
        job_id: UUID,
        worker_id: str,
        now: datetime,
    ) -> None:
        statement = (
            update(collection_jobs)
            .where(
                collection_jobs.c.id == job_id,
                collection_jobs.c.claimed_by == worker_id,
                collection_jobs.c.status == CollectionJobStatus.DOWNLOADING.value,
            )
            .values(status=CollectionJobStatus.VALIDATING.value, updated_at=now)
        )
        async with self._engine.begin() as connection:
            await connection.execute(statement)

    async def complete_collection(
        self,
        job_id: UUID,
        worker_id: str,
        *,
        sha256: str,
        document_type: DocumentType,
        media_type: str,
        size_bytes: int,
        document_id: UUID,
        artifact_object_id: UUID,
        safe_filename: str,
        now: datetime,
    ) -> CollectionJobRecord:
        async with self._engine.begin() as connection:
            workspace_id = (
                await connection.execute(
                    select(collection_jobs.c.workspace_id)
                    .where(
                        collection_jobs.c.id == job_id,
                        collection_jobs.c.claimed_by == worker_id,
                        collection_jobs.c.status == CollectionJobStatus.VALIDATING.value,
                    )
                    .with_for_update()
                )
            ).scalar_one()
            create_document = (
                postgresql_insert(documents)
                .values(
                    id=document_id,
                    workspace_id=workspace_id,
                    sha256=sha256,
                    document_type=document_type.value,
                    media_type=media_type,
                    size_bytes=size_bytes,
                    safe_filename=safe_filename,
                    created_at=now,
                    deleted_at=None,
                )
                .on_conflict_do_nothing(
                    index_elements=[documents.c.workspace_id, documents.c.sha256]
                )
                .returning(documents.c.id)
            )
            inserted_id = (await connection.execute(create_document)).scalar_one_or_none()
            duplicate = inserted_id is None
            if inserted_id is None:
                inserted_id = (
                    await connection.execute(
                        select(documents.c.id).where(
                            documents.c.workspace_id == workspace_id,
                            documents.c.sha256 == sha256,
                            documents.c.deleted_at.is_(None),
                        )
                    )
                ).scalar_one()
                discarded = await self._artifact_repository.discard_upload_in_connection(
                    connection,
                    artifact_object_id,
                    workspace_id,
                    now=now,
                )
                if discarded is None:
                    raise LookupError("The duplicate artifact upload does not exist.")
            else:
                await self._artifact_repository.finalize_reference_in_connection(
                    connection,
                    artifact_object_id,
                    workspace_id=workspace_id,
                    document_id=document_id,
                    candidate_analysis_id=None,
                    kind=ArtifactKind.STORED_DOCUMENT,
                    lifecycle=ArtifactLifecycle.PERSISTENT,
                    expires_at=None,
                    now=now,
                )
            statement = (
                update(collection_jobs)
                .where(
                    collection_jobs.c.id == job_id,
                    collection_jobs.c.claimed_by == worker_id,
                    collection_jobs.c.status == CollectionJobStatus.VALIDATING.value,
                )
                .values(
                    status=(
                        CollectionJobStatus.DUPLICATE.value
                        if duplicate
                        else CollectionJobStatus.COMPLETED.value
                    ),
                    document_id=inserted_id,
                    claimed_by=None,
                    lease_expires_at=None,
                    updated_at=now,
                    completed_at=now,
                )
                .returning(*collection_jobs.c)
            )
            row = (await connection.execute(statement)).mappings().one()
        return _collection_job_record(row)

    async def fail_collection(
        self,
        job_id: UUID,
        worker_id: str,
        *,
        error_code: str,
        error_detail: str,
        retryable: bool,
        retry_at: datetime | None,
        now: datetime,
    ) -> None:
        queued = retry_at is not None
        statement = (
            update(collection_jobs)
            .where(
                collection_jobs.c.id == job_id,
                collection_jobs.c.claimed_by == worker_id,
                collection_jobs.c.status.in_(
                    (
                        CollectionJobStatus.DOWNLOADING.value,
                        CollectionJobStatus.VALIDATING.value,
                    )
                ),
            )
            .values(
                status=(
                    CollectionJobStatus.QUEUED.value if queued else CollectionJobStatus.FAILED.value
                ),
                available_at=retry_at if retry_at is not None else now,
                claimed_by=None,
                lease_expires_at=None,
                error_code=error_code,
                error_detail=error_detail,
                error_retryable=retryable,
                updated_at=now,
                completed_at=None if queued else now,
            )
        )
        async with self._engine.begin() as connection:
            await connection.execute(statement)

    async def retry_collection_job(
        self,
        workspace_id: UUID,
        job_id: UUID,
        now: datetime,
    ) -> bool:
        statement = (
            update(collection_jobs)
            .where(
                collection_jobs.c.workspace_id == workspace_id,
                collection_jobs.c.id == job_id,
                collection_jobs.c.status == CollectionJobStatus.FAILED.value,
                collection_jobs.c.error_retryable.is_(True),
            )
            .values(
                status=CollectionJobStatus.QUEUED.value,
                available_at=now,
                claimed_by=None,
                lease_expires_at=None,
                error_code=None,
                error_detail=None,
                error_retryable=None,
                updated_at=now,
                completed_at=None,
            )
            .returning(collection_jobs.c.id)
        )
        async with self._engine.begin() as connection:
            return (await connection.execute(statement)).scalar_one_or_none() is not None

    async def list_documents(
        self,
        workspace_id: UUID,
        limit: int,
    ) -> tuple[StoredDocumentRecord, ...]:
        statement = (
            select(documents)
            .where(
                documents.c.workspace_id == workspace_id,
                documents.c.deleted_at.is_(None),
            )
            .order_by(documents.c.created_at.desc(), documents.c.id.desc())
            .limit(limit)
        )
        async with self._engine.connect() as connection:
            rows = (await connection.execute(statement)).mappings().all()
        return tuple(_stored_document_record(row) for row in rows)

    async def list_documents_page(
        self,
        workspace_id: UUID,
        limit: int,
        cursor: PageCursor | None,
    ) -> Page[StoredDocumentRecord]:
        statement = select(documents).where(
            documents.c.workspace_id == workspace_id,
            documents.c.deleted_at.is_(None),
        )
        if cursor is not None:
            statement = statement.where(
                or_(
                    documents.c.created_at < cursor.created_at,
                    and_(
                        documents.c.created_at == cursor.created_at,
                        documents.c.id < cursor.id,
                    ),
                )
            )
        statement = statement.order_by(
            documents.c.created_at.desc(),
            documents.c.id.desc(),
        ).limit(limit + 1)
        async with self._engine.connect() as connection:
            total = int(
                (
                    await connection.execute(
                        select(func.count())
                        .select_from(documents)
                        .where(
                            documents.c.workspace_id == workspace_id,
                            documents.c.deleted_at.is_(None),
                        )
                    )
                ).scalar_one()
            )
            rows = (await connection.execute(statement)).mappings().all()
        page_rows = rows[:limit]
        records = tuple(_stored_document_record(row) for row in page_rows)
        next_cursor = None
        if len(rows) > limit and records:
            last = records[-1]
            next_cursor = PageCursor(created_at=last.created_at, id=last.id)
        return Page(items=records, total=total, next_cursor=next_cursor)

    async def get_document(
        self,
        workspace_id: UUID,
        document_id: UUID,
    ) -> StoredDocumentRecord | None:
        statement = select(documents).where(
            documents.c.workspace_id == workspace_id,
            documents.c.id == document_id,
            documents.c.deleted_at.is_(None),
        )
        async with self._engine.connect() as connection:
            row = (await connection.execute(statement)).mappings().one_or_none()
        return None if row is None else _stored_document_record(row)

    async def delete_document(
        self,
        workspace_id: UUID,
        document_id: UUID,
        now: datetime,
    ) -> bool:
        async with self._engine.begin() as connection:
            row = (
                await connection.execute(
                    select(documents.c.deleted_at)
                    .where(
                        documents.c.workspace_id == workspace_id,
                        documents.c.id == document_id,
                    )
                    .with_for_update()
                )
            ).one_or_none()
            if row is None:
                return False
            if row.deleted_at is not None:
                return True
            await connection.execute(
                update(documents)
                .where(
                    documents.c.workspace_id == workspace_id,
                    documents.c.id == document_id,
                    documents.c.deleted_at.is_(None),
                )
                .values(deleted_at=now)
            )
            removed = await self._artifact_repository.remove_document_reference_in_connection(
                connection,
                workspace_id,
                document_id,
                now=now,
            )
            if removed is None:
                raise LookupError("The document artifact reference does not exist.")
        return True

    async def create_export(
        self,
        workspace_id: UUID,
        document_id: UUID,
        relative_directory: str,
        target_filename: str,
        now: datetime,
        created_by_user_id: UUID | None = None,
    ) -> DocumentExportRecord:
        statement = (
            insert(document_exports)
            .values(
                id=uuid4(),
                workspace_id=workspace_id,
                created_by_user_id=created_by_user_id,
                document_id=document_id,
                relative_directory=relative_directory,
                target_filename=target_filename,
                exported_relative_path=None,
                status=ExportStatus.QUEUED.value,
                attempt_count=0,
                available_at=now,
                claimed_by=None,
                lease_expires_at=None,
                error_code=None,
                error_detail=None,
                created_at=now,
                updated_at=now,
                completed_at=None,
            )
            .returning(*document_exports.c)
        )
        async with self._engine.begin() as connection:
            document_exists = (
                await connection.execute(
                    select(documents.c.id).where(
                        documents.c.workspace_id == workspace_id,
                        documents.c.id == document_id,
                        documents.c.deleted_at.is_(None),
                    )
                )
            ).scalar_one_or_none()
            if document_exists is None:
                raise LookupError("The workspace document does not exist.")
            row = (await connection.execute(statement)).mappings().one()
        return _export_record(row)

    async def claim_export(
        self,
        worker_id: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> DocumentExportRecord | None:
        eligible = or_(
            and_(
                document_exports.c.status == ExportStatus.QUEUED.value,
                document_exports.c.available_at <= now,
            ),
            and_(
                document_exports.c.status == ExportStatus.EXPORTING.value,
                document_exports.c.lease_expires_at < now,
            ),
        )
        claimable = (
            select(document_exports.c.id)
            .where(eligible)
            .order_by(document_exports.c.available_at, document_exports.c.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        async with self._engine.begin() as connection:
            export_id = (await connection.execute(claimable)).scalar_one_or_none()
            if export_id is None:
                return None
            statement = (
                update(document_exports)
                .where(document_exports.c.id == export_id)
                .values(
                    status=ExportStatus.EXPORTING.value,
                    attempt_count=document_exports.c.attempt_count + 1,
                    claimed_by=worker_id,
                    lease_expires_at=lease_expires_at,
                    error_code=None,
                    error_detail=None,
                    updated_at=now,
                    completed_at=None,
                )
                .returning(*document_exports.c)
            )
            row = (await connection.execute(statement)).mappings().one()
        return _export_record(row)

    async def complete_export(
        self,
        export_id: UUID,
        worker_id: str,
        exported_relative_path: str,
        now: datetime,
    ) -> DocumentExportRecord:
        statement = (
            update(document_exports)
            .where(
                document_exports.c.id == export_id,
                document_exports.c.claimed_by == worker_id,
                document_exports.c.status == ExportStatus.EXPORTING.value,
            )
            .values(
                status=ExportStatus.COMPLETED.value,
                exported_relative_path=exported_relative_path,
                claimed_by=None,
                lease_expires_at=None,
                updated_at=now,
                completed_at=now,
            )
            .returning(*document_exports.c)
        )
        async with self._engine.begin() as connection:
            row = (await connection.execute(statement)).mappings().one()
        return _export_record(row)

    async def renew_export_lease(
        self,
        export_id: UUID,
        worker_id: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> bool:
        statement = (
            update(document_exports)
            .where(
                document_exports.c.id == export_id,
                document_exports.c.claimed_by == worker_id,
                document_exports.c.status == ExportStatus.EXPORTING.value,
                document_exports.c.lease_expires_at >= now,
            )
            .values(lease_expires_at=lease_expires_at, updated_at=now)
            .returning(document_exports.c.id)
        )
        async with self._engine.begin() as connection:
            return (await connection.execute(statement)).scalar_one_or_none() is not None

    async def fail_export(
        self,
        export_id: UUID,
        worker_id: str,
        *,
        error_code: str,
        error_detail: str,
        retry_at: datetime | None,
        now: datetime,
    ) -> None:
        queued = retry_at is not None
        statement = (
            update(document_exports)
            .where(
                document_exports.c.id == export_id,
                document_exports.c.claimed_by == worker_id,
                document_exports.c.status == ExportStatus.EXPORTING.value,
            )
            .values(
                status=ExportStatus.QUEUED.value if queued else ExportStatus.FAILED.value,
                available_at=retry_at if retry_at is not None else now,
                claimed_by=None,
                lease_expires_at=None,
                error_code=error_code,
                error_detail=error_detail,
                updated_at=now,
                completed_at=None if queued else now,
            )
        )
        async with self._engine.begin() as connection:
            await connection.execute(statement)

    async def list_exports(
        self,
        workspace_id: UUID,
        limit: int,
    ) -> tuple[DocumentExportRecord, ...]:
        statement = (
            select(document_exports)
            .where(document_exports.c.workspace_id == workspace_id)
            .order_by(document_exports.c.created_at.desc(), document_exports.c.id.desc())
            .limit(limit)
        )
        async with self._engine.connect() as connection:
            rows = (await connection.execute(statement)).mappings().all()
        return tuple(_export_record(row) for row in rows)
