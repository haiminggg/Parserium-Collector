import hashlib
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import anyio

from parserium_collector.features.acquisition.downloader import DownloadResult
from parserium_collector.features.acquisition.errors import (
    BlockedDestinationError,
    DownloadTimeoutError,
)
from parserium_collector.features.acquisition.export_storage import ExportPlacement
from parserium_collector.features.acquisition.models import (
    CollectionJobRecord,
    CollectionJobStatus,
    DocumentExportRecord,
    DocumentType,
    ExportStatus,
    StoredDocumentRecord,
)
from parserium_collector.features.acquisition.validation import ValidatedDocument
from parserium_collector.features.acquisition.worker import AcquisitionWorker
from parserium_collector.features.storage.models import (
    ArtifactObjectState,
    StoredObjectMetadata,
)
from parserium_collector.features.storage.repository import ArtifactObjectRecord

NOW = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
CONTENT = b"test-only document bytes"
WORKSPACE_ID = UUID("10000000-0000-4000-8000-000000000001")
DOCUMENT_ID = UUID("20000000-0000-4000-8000-000000000001")
ARTIFACT_OBJECT_ID = UUID("30000000-0000-4000-8000-000000000001")


def collection_job(*, attempt_count: int = 1) -> CollectionJobRecord:
    return CollectionJobRecord(
        id=uuid4(),
        workspace_id=WORKSPACE_ID,
        source_url="https://documents.example/annual-report.pdf",
        title="Annual report",
        expected_document_type=DocumentType.PDF,
        status=CollectionJobStatus.DOWNLOADING,
        attempt_count=attempt_count,
        available_at=NOW,
        claimed_by="worker-test",
        lease_expires_at=NOW + timedelta(seconds=60),
        bytes_downloaded=0,
        content_length=None,
        document_id=None,
        error_code=None,
        error_detail=None,
        error_retryable=None,
        created_at=NOW,
        updated_at=NOW,
        started_at=NOW,
        completed_at=None,
    )


def stored_document() -> StoredDocumentRecord:
    digest = hashlib.sha256(CONTENT).hexdigest()
    return StoredDocumentRecord(
        id=uuid4(),
        workspace_id=WORKSPACE_ID,
        sha256=digest,
        document_type=DocumentType.PDF,
        media_type="application/pdf",
        size_bytes=len(CONTENT),
        safe_filename="annual-report.pdf",
        created_at=NOW,
    )


def export_job(document_id: UUID) -> DocumentExportRecord:
    return DocumentExportRecord(
        id=uuid4(),
        workspace_id=WORKSPACE_ID,
        document_id=document_id,
        relative_directory="bank/2026",
        target_filename="annual-report.pdf",
        exported_relative_path=None,
        status=ExportStatus.EXPORTING,
        attempt_count=1,
        available_at=NOW,
        claimed_by="worker-test",
        lease_expires_at=NOW + timedelta(seconds=60),
        error_code=None,
        error_detail=None,
        created_at=NOW,
        updated_at=NOW,
        completed_at=None,
    )


@dataclass
class FakeRepository:
    collection: CollectionJobRecord | None = None
    export: DocumentExportRecord | None = None
    document: StoredDocumentRecord | None = None
    events: list[str] = field(default_factory=list)
    progress: list[tuple[int, int | None]] = field(default_factory=list)
    collection_failure: dict[str, Any] | None = None
    export_failure: dict[str, Any] | None = None
    completion: dict[str, Any] | None = None
    renewed: anyio.Event = field(default_factory=anyio.Event)
    export_claim_calls: int = 0

    async def claim_collection_job(
        self,
        worker_id: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> CollectionJobRecord | None:
        record = self.collection
        self.collection = None
        return record

    async def renew_collection_lease(
        self,
        job_id: UUID,
        worker_id: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> bool:
        self.events.append("renew_collection")
        self.renewed.set()
        return True

    async def update_collection_progress(
        self,
        job_id: UUID,
        worker_id: str,
        bytes_downloaded: int,
        content_length: int | None,
        now: datetime,
    ) -> None:
        self.events.append("progress")
        self.progress.append((bytes_downloaded, content_length))

    async def mark_collection_validating(
        self,
        job_id: UUID,
        worker_id: str,
        now: datetime,
    ) -> None:
        self.events.append("validating")

    async def complete_collection(
        self,
        job_id: UUID,
        worker_id: str,
        **values: Any,
    ) -> CollectionJobRecord:
        self.events.append("complete_collection")
        self.completion = values
        return replace(
            collection_job(),
            id=job_id,
            status=CollectionJobStatus.COMPLETED,
            document_id=uuid4(),
        )

    async def fail_collection(
        self,
        job_id: UUID,
        worker_id: str,
        **values: Any,
    ) -> None:
        self.events.append("fail_collection")
        self.collection_failure = values

    async def claim_export(
        self,
        worker_id: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> DocumentExportRecord | None:
        self.export_claim_calls += 1
        record = self.export
        self.export = None
        return record

    async def renew_export_lease(
        self,
        export_id: UUID,
        worker_id: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> bool:
        self.events.append("renew_export")
        return True

    async def get_document(
        self,
        workspace_id: UUID,
        document_id: UUID,
    ) -> StoredDocumentRecord | None:
        return (
            self.document
            if self.document
            and self.document.workspace_id == workspace_id
            and self.document.id == document_id
            else None
        )

    async def complete_export(
        self,
        export_id: UUID,
        worker_id: str,
        exported_relative_path: str,
        now: datetime,
    ) -> DocumentExportRecord:
        self.events.append("complete_export")
        assert self.document is not None
        return replace(
            export_job(self.document.id),
            id=export_id,
            status=ExportStatus.COMPLETED,
            exported_relative_path=exported_relative_path,
        )

    async def fail_export(
        self,
        export_id: UUID,
        worker_id: str,
        **values: Any,
    ) -> None:
        self.events.append("fail_export")
        self.export_failure = values


@dataclass
class FakeStagedFile:
    path: Path = Path("/test-only/staged.part")
    content: bytes = b""
    finished: bool = False

    async def write(self, chunk: bytes) -> None:
        self.content += chunk

    async def finish(self) -> None:
        self.finished = True


def artifact_record(
    object_metadata: StoredObjectMetadata,
    *,
    state: ArtifactObjectState = ArtifactObjectState.UPLOADING,
) -> ArtifactObjectRecord:
    return ArtifactObjectRecord(
        id=ARTIFACT_OBJECT_ID,
        workspace_id=WORKSPACE_ID,
        storage_key=object_metadata.storage_key,
        media_type=object_metadata.media_type,
        size_bytes=object_metadata.size_bytes,
        sha256=object_metadata.sha256,
        state=state,
        available_at=NOW if state is ArtifactObjectState.AVAILABLE else None,
        delete_attempt_count=0,
        delete_available_at=None,
        delete_claimed_by=None,
        delete_lease_expires_at=None,
        failure_code=None,
        created_at=NOW,
        updated_at=NOW,
        deleted_at=None,
    )


@dataclass
class FakeArtifactRepository:
    events: list[str] = field(default_factory=list)
    uploads: list[ArtifactObjectRecord] = field(default_factory=list)
    document_object: ArtifactObjectRecord | None = None

    async def begin_upload(
        self,
        workspace_id: UUID,
        storage_key: str,
        object_metadata: StoredObjectMetadata,
        *,
        now: datetime,
    ) -> ArtifactObjectRecord:
        assert workspace_id == WORKSPACE_ID
        assert storage_key == object_metadata.storage_key
        self.events.append("begin_upload")
        record = artifact_record(object_metadata)
        self.uploads.append(record)
        return record

    async def resolve_document_object(
        self,
        workspace_id: UUID,
        document_id: UUID,
    ) -> ArtifactObjectRecord | None:
        return self.document_object if workspace_id == WORKSPACE_ID else None


@dataclass
class FakeArtifactStore:
    events: list[str] = field(default_factory=list)
    error: Exception | None = None
    uploads: list[StoredObjectMetadata] = field(default_factory=list)

    async def put_file(
        self,
        storage_key: str,
        source: Path,
        *,
        media_type: str,
        sha256: str,
        size_bytes: int,
    ) -> StoredObjectMetadata:
        self.events.append("put_file")
        if self.error is not None:
            raise self.error
        result = StoredObjectMetadata(
            storage_key=storage_key,
            media_type=media_type,
            size_bytes=size_bytes,
            sha256=sha256,
        )
        self.uploads.append(result)
        return result


@dataclass
class FakeScratchJob:
    storage: "FakeStorage"

    async def create_file(self, filename: str, *, max_bytes: int) -> FakeStagedFile:
        assert filename in {"source.pdf", "source.docx"}
        assert max_bytes >= len(CONTENT)
        return self.storage.staged

    async def materialize(
        self,
        store: object,
        storage_key: str,
        filename: str,
        *,
        max_bytes: int,
    ) -> Path:
        assert storage_key
        assert filename in {"source.pdf", "source.docx"}
        assert max_bytes >= len(CONTENT)
        self.storage.staged.content = CONTENT
        self.storage.staged.finished = True
        return self.storage.staged.path


@dataclass
class FakeStorage:
    staged: FakeStagedFile = field(default_factory=FakeStagedFile)
    cleaned: bool = False
    exported: bool = False
    workspace_ids: list[UUID] = field(default_factory=list)
    artifact_repository: FakeArtifactRepository = field(default_factory=FakeArtifactRepository)
    artifact_store: FakeArtifactStore = field(default_factory=FakeArtifactStore)

    @asynccontextmanager
    async def job(
        self,
        workspace_id: UUID,
        job_id: UUID,
    ) -> AsyncIterator[FakeScratchJob]:
        self.workspace_ids.append(workspace_id)
        try:
            yield FakeScratchJob(self)
        finally:
            self.cleaned = True

    async def export_document(
        self,
        workspace_id: UUID,
        document_path: Path,
        relative_directory: str,
        target_filename: str,
        document_type: DocumentType,
    ) -> ExportPlacement:
        self.workspace_ids.append(workspace_id)
        self.exported = True
        return ExportPlacement(
            relative_path="bank/2026/annual-report.pdf",
            path=Path("/exports/bank/2026/annual-report.pdf"),
        )


@dataclass
class FakeDownloader:
    error: Exception | None = None
    wait_for: anyio.Event | None = None
    block: bool = False
    started: anyio.Event = field(default_factory=anyio.Event)

    async def download(
        self,
        url: str,
        sink: FakeStagedFile,
        *,
        progress: Callable[[int, int | None], Awaitable[None]] | None = None,
    ) -> DownloadResult:
        self.started.set()
        if self.block:
            await anyio.sleep_forever()
        if self.wait_for is not None:
            await self.wait_for.wait()
        if self.error is not None:
            raise self.error
        await sink.write(CONTENT)
        if progress is not None:
            await progress(len(CONTENT), len(CONTENT))
        return DownloadResult(
            final_url=url,
            sha256=hashlib.sha256(CONTENT).hexdigest(),
            size_bytes=len(CONTENT),
            content_length=len(CONTENT),
            media_type="application/octet-stream",
        )

    async def aclose(self) -> None:
        return None


class FakeValidator:
    def validate(self, path: Path, expected_type: DocumentType) -> ValidatedDocument:
        return ValidatedDocument(
            document_type=expected_type,
            media_type="application/pdf",
        )


class ImmediateThenBlock:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, seconds: float) -> None:
        self.calls += 1
        if self.calls == 1:
            return
        await anyio.sleep_forever()


def worker_for(
    repository: FakeRepository,
    *,
    downloader: FakeDownloader | None = None,
    storage: FakeStorage | None = None,
    export_enabled: bool = True,
    lease_waiter: Callable[[float], Awaitable[None]] = anyio.sleep,
) -> tuple[AcquisitionWorker, FakeDownloader, FakeStorage]:
    resolved_downloader = downloader or FakeDownloader()
    resolved_storage = storage or FakeStorage()
    resolved_storage.artifact_repository.events = repository.events
    resolved_storage.artifact_store.events = repository.events
    if repository.document is not None:
        digest = hashlib.sha256(CONTENT).hexdigest()
        resolved_storage.artifact_repository.document_object = artifact_record(
            StoredObjectMetadata(
                storage_key=(
                    f"workspaces/{WORKSPACE_ID}/document/{repository.document.id}/stored_document"
                ),
                media_type=repository.document.media_type,
                size_bytes=repository.document.size_bytes,
                sha256=digest,
            ),
            state=ArtifactObjectState.AVAILABLE,
        )
    return (
        AcquisitionWorker(
            repository=repository,
            artifact_repository=resolved_storage.artifact_repository,
            artifact_store=resolved_storage.artifact_store,
            scratch_storage=resolved_storage,
            export_storage=resolved_storage if export_enabled else None,
            downloader=resolved_downloader,
            validator=FakeValidator(),
            worker_id="worker-test",
            lease_seconds=60,
            max_attempts=3,
            retry_base_seconds=30,
            max_staging_bytes=1024,
            clock=lambda: NOW,
            lease_waiter=lease_waiter,
            id_factory=lambda: DOCUMENT_ID,
        ),
        resolved_downloader,
        resolved_storage,
    )


async def test_collection_moves_through_progress_validation_and_completion() -> None:
    repository = FakeRepository(collection=collection_job())
    worker, _, storage = worker_for(repository)

    handled = await worker.run_once()

    assert handled is True
    assert repository.events == [
        "progress",
        "validating",
        "begin_upload",
        "put_file",
        "complete_collection",
    ]
    assert repository.progress == [(len(CONTENT), len(CONTENT))]
    assert repository.completion is not None
    assert repository.completion["sha256"] == hashlib.sha256(CONTENT).hexdigest()
    assert repository.completion["safe_filename"] == "Annual report.pdf"
    assert repository.completion["document_id"] == DOCUMENT_ID
    assert repository.completion["artifact_object_id"] == ARTIFACT_OBJECT_ID
    assert storage.artifact_repository.uploads[0].storage_key == (
        f"workspaces/{WORKSPACE_ID}/document/{DOCUMENT_ID}/stored_document"
    )
    assert storage.workspace_ids == [WORKSPACE_ID]
    assert storage.staged.finished is True
    assert storage.cleaned is True


async def test_collection_jobs_take_priority_over_export_jobs() -> None:
    document = stored_document()
    repository = FakeRepository(
        collection=collection_job(),
        export=export_job(document.id),
        document=document,
    )
    worker, _, _ = worker_for(repository)

    await worker.run_once()

    assert repository.export_claim_calls == 0


async def test_retryable_failure_is_scheduled_with_backoff() -> None:
    repository = FakeRepository(collection=collection_job(attempt_count=1))
    worker, _, storage = worker_for(
        repository,
        downloader=FakeDownloader(error=DownloadTimeoutError("Timed out.")),
    )

    await worker.run_once()

    assert repository.collection_failure is not None
    assert repository.collection_failure["error_code"] == "download_timeout"
    assert repository.collection_failure["retryable"] is True
    assert repository.collection_failure["retry_at"] == NOW + timedelta(seconds=30)
    assert storage.cleaned is True


async def test_failed_durable_upload_leaves_registry_row_for_maintenance() -> None:
    repository = FakeRepository(collection=collection_job())
    storage = FakeStorage()
    storage.artifact_store.error = RuntimeError("test-only provider failure")
    worker, _, storage = worker_for(repository, storage=storage)

    await worker.run_once()

    assert len(storage.artifact_repository.uploads) == 1
    assert storage.artifact_repository.uploads[0].state is ArtifactObjectState.UPLOADING
    assert storage.cleaned is True
    assert repository.completion is None
    assert repository.collection_failure is not None
    assert repository.collection_failure["error_code"] == "internal_error"


async def test_policy_failure_is_terminal() -> None:
    repository = FakeRepository(collection=collection_job(attempt_count=1))
    worker, _, _ = worker_for(
        repository,
        downloader=FakeDownloader(error=BlockedDestinationError("Blocked.")),
    )

    await worker.run_once()

    assert repository.collection_failure is not None
    assert repository.collection_failure["error_code"] == "blocked_destination"
    assert repository.collection_failure["retryable"] is False
    assert repository.collection_failure["retry_at"] is None


async def test_last_automatic_attempt_becomes_a_retryable_terminal_failure() -> None:
    repository = FakeRepository(collection=collection_job(attempt_count=3))
    worker, _, _ = worker_for(
        repository,
        downloader=FakeDownloader(error=DownloadTimeoutError("Timed out.")),
    )

    await worker.run_once()

    assert repository.collection_failure is not None
    assert repository.collection_failure["retryable"] is True
    assert repository.collection_failure["retry_at"] is None


async def test_live_lease_is_renewed_during_collection() -> None:
    repository = FakeRepository(collection=collection_job())
    waiter = ImmediateThenBlock()
    worker, _, _ = worker_for(
        repository,
        downloader=FakeDownloader(wait_for=repository.renewed),
        lease_waiter=waiter,
    )

    await worker.run_once()

    assert "renew_collection" in repository.events


async def test_export_is_processed_when_no_collection_is_available() -> None:
    document = stored_document()
    repository = FakeRepository(export=export_job(document.id), document=document)
    worker, _, storage = worker_for(repository)

    handled = await worker.run_once()

    assert handled is True
    assert storage.exported is True
    assert storage.workspace_ids == [WORKSPACE_ID, WORKSPACE_ID]
    assert repository.events == ["complete_export"]


async def test_missing_export_document_fails_safely() -> None:
    missing_document_id = uuid4()
    repository = FakeRepository(export=export_job(missing_document_id))
    worker, _, _ = worker_for(repository)

    await worker.run_once()

    assert repository.export_failure is not None
    assert repository.export_failure["error_code"] == "document_not_found"
    assert repository.export_failure["retry_at"] is None


async def test_export_fails_safely_when_local_export_storage_is_omitted() -> None:
    document = stored_document()
    repository = FakeRepository(export=export_job(document.id), document=document)
    worker, _, storage = worker_for(repository, export_enabled=False)

    await worker.run_once()

    assert repository.export_failure is not None
    assert repository.export_failure["error_code"] == "export_unavailable"
    assert repository.export_failure["retry_at"] is None
    assert storage.workspace_ids == []


async def test_cancellation_cleans_partial_staging() -> None:
    repository = FakeRepository(collection=collection_job())
    downloader = FakeDownloader(block=True)
    worker, _, storage = worker_for(repository, downloader=downloader)

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(worker.run_once)
        await downloader.started.wait()
        task_group.cancel_scope.cancel()

    assert storage.cleaned is True


async def test_no_available_work_returns_false() -> None:
    worker, _, _ = worker_for(FakeRepository())

    assert await worker.run_once() is False
