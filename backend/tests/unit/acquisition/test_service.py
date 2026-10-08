from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from parserium_collector.features.acquisition.errors import (
    CollectionJobNotFoundError,
    CollectionRetryConflictError,
    DocumentNotFoundError,
    ExportUnavailableError,
)
from parserium_collector.features.acquisition.models import (
    CollectionBatchRequest,
    CollectionCandidate,
    CollectionJobRecord,
    CollectionJobStatus,
    DocumentExportRecord,
    DocumentType,
    ExportStatus,
    StoredDocumentRecord,
)
from parserium_collector.features.acquisition.pagination import Page, PageCursor
from parserium_collector.features.acquisition.service import AcquisitionService
from parserium_collector.features.identity.models import WorkspaceRole, WorkspaceScope

NOW = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
WORKSPACE_A = uuid4()
WORKSPACE_B = uuid4()
SCOPE_A = WorkspaceScope(
    workspace_id=WORKSPACE_A,
    user_id=uuid4(),
    role=WorkspaceRole.OWNER,
)
SCOPE_B = WorkspaceScope(
    workspace_id=WORKSPACE_B,
    user_id=uuid4(),
    role=WorkspaceRole.OWNER,
)


def job_record(
    *,
    workspace_id: UUID = WORKSPACE_A,
    status: CollectionJobStatus = CollectionJobStatus.QUEUED,
    retryable: bool | None = None,
) -> CollectionJobRecord:
    return CollectionJobRecord(
        id=uuid4(),
        workspace_id=workspace_id,
        source_url="https://documents.example/report.pdf",
        title="Annual report",
        expected_document_type=DocumentType.PDF,
        status=status,
        attempt_count=1,
        available_at=NOW,
        claimed_by=None,
        lease_expires_at=None,
        bytes_downloaded=0,
        content_length=None,
        document_id=None,
        error_code="download_timeout" if status is CollectionJobStatus.FAILED else None,
        error_detail="The document server timed out."
        if status is CollectionJobStatus.FAILED
        else None,
        error_retryable=retryable,
        created_at=NOW,
        updated_at=NOW,
        started_at=None,
        completed_at=NOW if status is CollectionJobStatus.FAILED else None,
    )


def document_record(*, workspace_id: UUID = WORKSPACE_A) -> StoredDocumentRecord:
    return StoredDocumentRecord(
        id=uuid4(),
        workspace_id=workspace_id,
        sha256="a" * 64,
        document_type=DocumentType.PDF,
        media_type="application/pdf",
        size_bytes=1024,
        safe_filename="annual-report.pdf",
        created_at=NOW,
    )


class InMemoryAcquisitionRepository:
    def __init__(self) -> None:
        self.jobs: dict[UUID, CollectionJobRecord] = {}
        self.documents: dict[UUID, StoredDocumentRecord] = {}
        self.exports: dict[UUID, DocumentExportRecord] = {}
        self.created_candidates: tuple[CollectionCandidate, ...] = ()
        self.created_by_user_id: UUID | None = None
        self.deleted_documents: set[tuple[UUID, UUID]] = set()

    async def create_collection_jobs(
        self,
        workspace_id: UUID,
        candidates: tuple[CollectionCandidate, ...],
        now: datetime,
        created_by_user_id: UUID | None = None,
    ) -> tuple[CollectionJobRecord, ...]:
        self.created_candidates = candidates
        self.created_by_user_id = created_by_user_id
        created = tuple(job_record(workspace_id=workspace_id) for _ in candidates)
        self.jobs.update((job.id, job) for job in created)
        return created

    async def get_collection_job(
        self,
        workspace_id: UUID,
        job_id: UUID,
    ) -> CollectionJobRecord | None:
        job = self.jobs.get(job_id)
        return job if job is not None and job.workspace_id == workspace_id else None

    async def list_collection_jobs(
        self,
        workspace_id: UUID,
        limit: int,
    ) -> tuple[CollectionJobRecord, ...]:
        return tuple(job for job in self.jobs.values() if job.workspace_id == workspace_id)[:limit]

    async def list_collection_jobs_page(
        self,
        workspace_id: UUID,
        limit: int,
        cursor: PageCursor | None,
    ) -> Page[CollectionJobRecord]:
        assert cursor is None
        records = tuple(job for job in self.jobs.values() if job.workspace_id == workspace_id)
        return Page(items=records[:limit], total=len(records), next_cursor=None)

    async def retry_collection_job(
        self,
        workspace_id: UUID,
        job_id: UUID,
        now: datetime,
    ) -> bool:
        job = await self.get_collection_job(workspace_id, job_id)
        if (
            job is None
            or job.status is not CollectionJobStatus.FAILED
            or job.error_retryable is not True
        ):
            return False
        self.jobs[job_id] = replace(
            job,
            status=CollectionJobStatus.QUEUED,
            error_code=None,
            error_detail=None,
            error_retryable=None,
            completed_at=None,
            updated_at=now,
        )
        return True

    async def delete_completed_collection_jobs(self, workspace_id: UUID) -> int:
        completed_statuses = {
            CollectionJobStatus.COMPLETED,
            CollectionJobStatus.DUPLICATE,
        }
        completed_ids = {
            job_id
            for job_id, job in self.jobs.items()
            if job.workspace_id == workspace_id and job.status in completed_statuses
        }
        for job_id in completed_ids:
            del self.jobs[job_id]
        return len(completed_ids)

    async def get_document(
        self,
        workspace_id: UUID,
        document_id: UUID,
    ) -> StoredDocumentRecord | None:
        document = self.documents.get(document_id)
        return document if document is not None and document.workspace_id == workspace_id else None

    async def delete_document(
        self,
        workspace_id: UUID,
        document_id: UUID,
        now: datetime,
    ) -> bool:
        document = await self.get_document(workspace_id, document_id)
        if document is not None:
            del self.documents[document_id]
            self.deleted_documents.add((workspace_id, document_id))
            return True
        return (workspace_id, document_id) in self.deleted_documents

    async def list_documents(
        self,
        workspace_id: UUID,
        limit: int,
    ) -> tuple[StoredDocumentRecord, ...]:
        return tuple(
            document
            for document in self.documents.values()
            if document.workspace_id == workspace_id
        )[:limit]

    async def list_documents_page(
        self,
        workspace_id: UUID,
        limit: int,
        cursor: PageCursor | None,
    ) -> Page[StoredDocumentRecord]:
        assert cursor is None
        records = tuple(
            document
            for document in self.documents.values()
            if document.workspace_id == workspace_id
        )
        return Page(items=records[:limit], total=len(records), next_cursor=None)

    async def create_export(
        self,
        workspace_id: UUID,
        document_id: UUID,
        relative_directory: str,
        target_filename: str,
        now: datetime,
        created_by_user_id: UUID | None = None,
    ) -> DocumentExportRecord:
        self.created_by_user_id = created_by_user_id
        record = DocumentExportRecord(
            id=uuid4(),
            workspace_id=workspace_id,
            document_id=document_id,
            relative_directory=relative_directory,
            target_filename=target_filename,
            exported_relative_path=None,
            status=ExportStatus.QUEUED,
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
        self.exports[record.id] = record
        return record

    async def list_exports(
        self,
        workspace_id: UUID,
        limit: int,
    ) -> tuple[DocumentExportRecord, ...]:
        return tuple(
            export for export in self.exports.values() if export.workspace_id == workspace_id
        )[:limit]


class TestArtifactAccess:
    async def open_document(
        self,
        workspace_id: UUID,
        document_id: UUID,
        filename: str,
    ) -> None:
        raise AssertionError("A foreign document must not reach artifact access.")


async def test_batch_creation_preserves_selected_discovery_metadata() -> None:
    repository = InMemoryAcquisitionRepository()
    service = AcquisitionService(
        repository,
        export_available=True,
        artifact_access=TestArtifactAccess(),  # type: ignore[arg-type]
    )
    request = CollectionBatchRequest(
        candidates=(
            CollectionCandidate(
                url="https://bank.example/reports/annual.pdf",
                title="Bank annual report",
                document_type=DocumentType.PDF,
            ),
            CollectionCandidate(
                url="https://bank.example/reports/quarterly.docx",
                title="Bank quarterly report",
                document_type=DocumentType.DOCX,
            ),
        )
    )

    created = await service.create_collection(SCOPE_A, request, NOW)

    assert len(created) == 2
    assert repository.created_candidates == request.candidates
    assert repository.created_by_user_id == SCOPE_A.user_id


async def test_job_listing_is_bounded() -> None:
    repository = InMemoryAcquisitionRepository()
    repository.jobs = {job.id: job for job in (job_record(), job_record())}
    service = AcquisitionService(repository, export_available=True)

    listed = await service.list_collection_jobs(SCOPE_A, limit=1)

    assert len(listed) == 1


async def test_job_page_includes_the_explicit_total() -> None:
    repository = InMemoryAcquisitionRepository()
    repository.jobs = {job.id: job for job in (job_record(), job_record())}
    service = AcquisitionService(repository, export_available=True)

    page = await service.list_collection_jobs_page(SCOPE_A, limit=1, cursor=None)

    assert len(page.items) == 1
    assert page.total == 2


async def test_missing_job_cannot_be_retried() -> None:
    service = AcquisitionService(InMemoryAcquisitionRepository(), export_available=True)

    with pytest.raises(CollectionJobNotFoundError):
        await service.retry_collection(SCOPE_A, uuid4(), NOW)


@pytest.mark.parametrize(
    "record",
    (
        job_record(status=CollectionJobStatus.COMPLETED),
        job_record(status=CollectionJobStatus.FAILED, retryable=False),
    ),
)
async def test_ineligible_job_retry_is_a_conflict(record: CollectionJobRecord) -> None:
    repository = InMemoryAcquisitionRepository()
    repository.jobs[record.id] = record
    service = AcquisitionService(repository, export_available=True)

    with pytest.raises(CollectionRetryConflictError):
        await service.retry_collection(SCOPE_A, record.id, NOW)


async def test_retryable_failure_is_requeued() -> None:
    repository = InMemoryAcquisitionRepository()
    record = job_record(status=CollectionJobStatus.FAILED, retryable=True)
    repository.jobs[record.id] = record
    service = AcquisitionService(repository, export_available=True)

    retried = await service.retry_collection(SCOPE_A, record.id, NOW)

    assert retried.status is CollectionJobStatus.QUEUED
    assert retried.error_code is None


async def test_clear_completed_jobs_preserves_active_and_failed_history() -> None:
    repository = InMemoryAcquisitionRepository()
    records = (
        job_record(status=CollectionJobStatus.COMPLETED),
        job_record(status=CollectionJobStatus.DUPLICATE),
        job_record(status=CollectionJobStatus.QUEUED),
        job_record(status=CollectionJobStatus.FAILED, retryable=True),
    )
    repository.jobs = {record.id: record for record in records}
    service = AcquisitionService(repository, export_available=True)

    deleted_count = await service.clear_completed_collection_jobs(SCOPE_A)

    assert deleted_count == 2
    assert {job.status for job in repository.jobs.values()} == {
        CollectionJobStatus.QUEUED,
        CollectionJobStatus.FAILED,
    }


async def test_document_listing_is_bounded() -> None:
    repository = InMemoryAcquisitionRepository()
    records = (document_record(), document_record())
    repository.documents = {record.id: record for record in records}
    service = AcquisitionService(repository, export_available=True)

    listed = await service.list_documents(SCOPE_A, limit=1)

    assert len(listed) == 1


async def test_document_page_includes_the_explicit_total() -> None:
    repository = InMemoryAcquisitionRepository()
    records = (document_record(), document_record())
    repository.documents = {record.id: record for record in records}
    service = AcquisitionService(repository, export_available=True)

    page = await service.list_documents_page(SCOPE_A, limit=1, cursor=None)

    assert len(page.items) == 1
    assert page.total == 2


async def test_export_requires_configuration() -> None:
    repository = InMemoryAcquisitionRepository()
    document = document_record()
    repository.documents[document.id] = document
    service = AcquisitionService(repository, export_available=False)

    with pytest.raises(ExportUnavailableError):
        await service.create_export(SCOPE_A, document.id, "bank/2026", NOW)


async def test_export_requires_an_existing_document() -> None:
    service = AcquisitionService(InMemoryAcquisitionRepository(), export_available=True)

    with pytest.raises(DocumentNotFoundError):
        await service.create_export(SCOPE_A, uuid4(), "bank/2026", NOW)


async def test_export_uses_the_documents_safe_filename() -> None:
    repository = InMemoryAcquisitionRepository()
    document = document_record()
    repository.documents[document.id] = document
    service = AcquisitionService(repository, export_available=True)

    export = await service.create_export(SCOPE_A, document.id, "bank/2026", NOW)

    assert export.document_id == document.id
    assert export.relative_directory == "bank/2026"
    assert export.target_filename == document.safe_filename
    assert repository.created_by_user_id == SCOPE_A.user_id


async def test_cross_workspace_records_are_not_visible_or_mutable() -> None:
    repository = InMemoryAcquisitionRepository()
    foreign_job = job_record(
        workspace_id=WORKSPACE_B,
        status=CollectionJobStatus.FAILED,
        retryable=True,
    )
    foreign_document = document_record(workspace_id=WORKSPACE_B)
    repository.jobs[foreign_job.id] = foreign_job
    repository.documents[foreign_document.id] = foreign_document
    service = AcquisitionService(
        repository,
        export_available=True,
        artifact_access=TestArtifactAccess(),  # type: ignore[arg-type]
    )

    jobs = await service.list_collection_jobs_page(SCOPE_A, limit=10, cursor=None)
    documents = await service.list_documents_page(SCOPE_A, limit=10, cursor=None)

    assert jobs.items == ()
    assert jobs.total == 0
    assert documents.items == ()
    assert documents.total == 0
    with pytest.raises(CollectionJobNotFoundError):
        await service.retry_collection(SCOPE_A, foreign_job.id, NOW)
    with pytest.raises(DocumentNotFoundError):
        await service.create_export(SCOPE_A, foreign_document.id, "bank/2026", NOW)
    with pytest.raises(DocumentNotFoundError):
        await service.download_document(SCOPE_A, foreign_document.id)


async def test_document_deletion_is_owned_immediate_and_idempotent() -> None:
    repository = InMemoryAcquisitionRepository()
    document = document_record()
    repository.documents[document.id] = document
    service = AcquisitionService(repository, export_available=True)

    await service.delete_document(SCOPE_A, document.id, NOW)
    await service.delete_document(SCOPE_A, document.id, NOW)

    assert await service.list_documents(SCOPE_A, 10) == ()
    with pytest.raises(DocumentNotFoundError):
        await service.download_document(SCOPE_A, document.id)
    with pytest.raises(DocumentNotFoundError):
        await service.delete_document(SCOPE_B, document.id, NOW)
