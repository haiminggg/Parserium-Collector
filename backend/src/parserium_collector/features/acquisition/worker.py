from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Protocol
from urllib.parse import unquote, urlsplit
from uuid import UUID, uuid4

import anyio

from parserium_collector.features.acquisition.downloader import (
    AsyncByteSink,
    DownloadResult,
    ProgressCallback,
)
from parserium_collector.features.acquisition.errors import (
    AcquisitionError,
    DocumentNotFoundError,
    ExportUnavailableError,
)
from parserium_collector.features.acquisition.export_storage import (
    ExportPlacement,
    safe_windows_filename,
)
from parserium_collector.features.acquisition.models import (
    CollectionJobRecord,
    DocumentExportRecord,
    DocumentType,
)
from parserium_collector.features.acquisition.repository import AcquisitionRepository
from parserium_collector.features.acquisition.validation import ValidatedDocument
from parserium_collector.features.storage.errors import ArtifactIntegrityError
from parserium_collector.features.storage.keys import artifact_key
from parserium_collector.features.storage.models import (
    ArtifactKind,
    ArtifactResourceKind,
    StoredObjectMetadata,
)
from parserium_collector.features.storage.protocols import ArtifactStore
from parserium_collector.features.storage.repository import ArtifactObjectRecord


class StagedDownload(AsyncByteSink, Protocol):
    path: Path

    async def finish(self) -> None: ...


class WorkerDownloader(Protocol):
    async def download(
        self,
        url: str,
        sink: AsyncByteSink,
        *,
        progress: ProgressCallback | None = None,
    ) -> DownloadResult: ...

    async def aclose(self) -> None: ...


class WorkerValidator(Protocol):
    def validate(self, path: Path, expected_type: DocumentType) -> ValidatedDocument: ...


class WorkerScratchJob(Protocol):
    async def create_file(self, filename: str, *, max_bytes: int) -> StagedDownload: ...

    async def materialize(
        self,
        store: ArtifactStore,
        storage_key: str,
        filename: str,
        *,
        max_bytes: int,
    ) -> Path: ...


class WorkerScratchStorage(Protocol):
    def job(
        self,
        workspace_id: UUID,
        job_id: UUID,
    ) -> AbstractAsyncContextManager[WorkerScratchJob]: ...


class WorkerArtifactRepository(Protocol):
    async def begin_upload(
        self,
        workspace_id: UUID,
        storage_key: str,
        object_metadata: StoredObjectMetadata,
        *,
        now: datetime,
    ) -> ArtifactObjectRecord: ...

    async def resolve_document_object(
        self,
        workspace_id: UUID,
        document_id: UUID,
    ) -> ArtifactObjectRecord | None: ...


class WorkerExportStorage(Protocol):
    async def export_document(
        self,
        workspace_id: UUID,
        document_path: Path,
        relative_directory: str,
        target_filename: str,
        document_type: DocumentType,
    ) -> ExportPlacement: ...


class LeaseLostError(RuntimeError):
    pass


class AcquisitionWorker:
    def __init__(
        self,
        *,
        repository: AcquisitionRepository,
        artifact_repository: WorkerArtifactRepository,
        artifact_store: ArtifactStore,
        scratch_storage: WorkerScratchStorage,
        export_storage: WorkerExportStorage | None,
        downloader: WorkerDownloader,
        validator: WorkerValidator,
        worker_id: str,
        lease_seconds: int,
        max_attempts: int,
        retry_base_seconds: int,
        max_staging_bytes: int,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        lease_waiter: Callable[[float], Awaitable[None]] = anyio.sleep,
        id_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        if lease_seconds < 1 or max_attempts < 1 or retry_base_seconds < 1 or max_staging_bytes < 1:
            raise ValueError("Worker lease and retry settings must be positive.")
        self._repository = repository
        self._artifact_repository = artifact_repository
        self._artifact_store = artifact_store
        self._scratch_storage = scratch_storage
        self._export_storage = export_storage
        self._downloader = downloader
        self._validator = validator
        self._worker_id = worker_id
        self._lease_seconds = lease_seconds
        self._lease_interval = lease_seconds / 3
        self._max_attempts = max_attempts
        self._retry_base_seconds = retry_base_seconds
        self._max_staging_bytes = max_staging_bytes
        self._clock = clock
        self._lease_waiter = lease_waiter
        self._id_factory = id_factory

    async def run_once(self) -> bool:
        now = self._clock()
        job = await self._repository.claim_collection_job(
            self._worker_id,
            now,
            now + timedelta(seconds=self._lease_seconds),
        )
        if job is not None:
            await self._handle_collection(job)
            return True

        export = await self._repository.claim_export(
            self._worker_id,
            now,
            now + timedelta(seconds=self._lease_seconds),
        )
        if export is not None:
            await self._handle_export(export)
            return True
        return False

    async def aclose(self) -> None:
        await self._downloader.aclose()

    async def _handle_collection(self, job: CollectionJobRecord) -> None:
        async def operation() -> None:
            await self._process_collection(job)

        async def renew(now: datetime, lease_expires_at: datetime) -> bool:
            return await self._repository.renew_collection_lease(
                job.id,
                self._worker_id,
                now,
                lease_expires_at,
            )

        try:
            await self._run_with_lease(operation, renew)
        except LeaseLostError:
            return
        except AcquisitionError as error:
            await self._fail_collection(job, error)
        except Exception:
            await self._repository.fail_collection(
                job.id,
                self._worker_id,
                error_code="internal_error",
                error_detail="The collection worker encountered an internal error.",
                retryable=False,
                retry_at=None,
                now=self._clock(),
            )

    async def _process_collection(self, job: CollectionJobRecord) -> None:
        async with self._scratch_storage.job(job.workspace_id, job.id) as scratch:
            staged = await scratch.create_file(
                f"source.{job.expected_document_type.value}",
                max_bytes=self._max_staging_bytes,
            )

            async def update_progress(downloaded: int, total: int | None) -> None:
                await self._repository.update_collection_progress(
                    job.id,
                    self._worker_id,
                    downloaded,
                    total,
                    self._clock(),
                )

            result = await self._downloader.download(
                job.source_url,
                staged,
                progress=update_progress,
            )
            await staged.finish()
            await self._repository.mark_collection_validating(
                job.id,
                self._worker_id,
                self._clock(),
            )
            validated = await anyio.to_thread.run_sync(
                self._validator.validate,
                staged.path,
                job.expected_document_type,
            )
            document_id = self._id_factory()
            storage_key = artifact_key(
                job.workspace_id,
                ArtifactResourceKind.DOCUMENT,
                document_id,
                ArtifactKind.STORED_DOCUMENT,
            )
            expected_metadata = StoredObjectMetadata(
                storage_key=storage_key,
                media_type=validated.media_type,
                size_bytes=result.size_bytes,
                sha256=result.sha256,
            )
            artifact_object = await self._artifact_repository.begin_upload(
                job.workspace_id,
                storage_key,
                expected_metadata,
                now=self._clock(),
            )
            stored_metadata = await self._artifact_store.put_file(
                storage_key,
                staged.path,
                media_type=validated.media_type,
                sha256=result.sha256,
                size_bytes=result.size_bytes,
            )
            if stored_metadata != expected_metadata:
                raise ArtifactIntegrityError("Stored artifact metadata did not match the upload.")
            await self._repository.complete_collection(
                job.id,
                self._worker_id,
                sha256=result.sha256,
                document_type=validated.document_type,
                media_type=stored_metadata.media_type,
                size_bytes=stored_metadata.size_bytes,
                document_id=document_id,
                artifact_object_id=artifact_object.id,
                safe_filename=self._safe_filename(job),
                now=self._clock(),
            )

    async def _fail_collection(
        self,
        job: CollectionJobRecord,
        error: AcquisitionError,
    ) -> None:
        now = self._clock()
        await self._repository.fail_collection(
            job.id,
            self._worker_id,
            error_code=error.code,
            error_detail=self._safe_error_detail(error),
            retryable=error.retryable,
            retry_at=self._retry_at(job.attempt_count, error.retryable, now),
            now=now,
        )

    async def _handle_export(self, export: DocumentExportRecord) -> None:
        async def operation() -> None:
            await self._process_export(export)

        async def renew(now: datetime, lease_expires_at: datetime) -> bool:
            return await self._repository.renew_export_lease(
                export.id,
                self._worker_id,
                now,
                lease_expires_at,
            )

        try:
            await self._run_with_lease(operation, renew)
        except LeaseLostError:
            return
        except AcquisitionError as error:
            now = self._clock()
            await self._repository.fail_export(
                export.id,
                self._worker_id,
                error_code=error.code,
                error_detail=self._safe_error_detail(error),
                retry_at=self._retry_at(export.attempt_count, error.retryable, now),
                now=now,
            )
        except Exception:
            await self._repository.fail_export(
                export.id,
                self._worker_id,
                error_code="internal_error",
                error_detail="The export worker encountered an internal error.",
                retry_at=None,
                now=self._clock(),
            )

    async def _process_export(self, export: DocumentExportRecord) -> None:
        if self._export_storage is None:
            raise ExportUnavailableError("Document export is not configured.")
        document = await self._repository.get_document(
            export.workspace_id,
            export.document_id,
        )
        if document is None:
            raise DocumentNotFoundError("The document does not exist.")
        artifact_object = await self._artifact_repository.resolve_document_object(
            export.workspace_id,
            document.id,
        )
        if artifact_object is None:
            raise DocumentNotFoundError("The document artifact does not exist.")
        async with self._scratch_storage.job(export.workspace_id, export.id) as scratch:
            source = await scratch.materialize(
                self._artifact_store,
                artifact_object.storage_key,
                f"source.{document.document_type.value}",
                max_bytes=max(document.size_bytes, 1),
            )
            placement = await self._export_storage.export_document(
                export.workspace_id,
                source,
                export.relative_directory,
                export.target_filename,
                document.document_type,
            )
        await self._repository.complete_export(
            export.id,
            self._worker_id,
            placement.relative_path,
            self._clock(),
        )

    async def _run_with_lease(
        self,
        operation: Callable[[], Awaitable[None]],
        renew: Callable[[datetime, datetime], Awaitable[bool]],
    ) -> None:
        completed = anyio.Event()
        errors: list[BaseException] = []

        async def run_operation() -> None:
            try:
                await operation()
            except BaseException as error:
                errors.append(error)
            finally:
                completed.set()

        async def renew_lease() -> None:
            while True:
                await self._lease_waiter(self._lease_interval)
                if completed.is_set():
                    return
                now = self._clock()
                try:
                    renewed = await renew(
                        now,
                        now + timedelta(seconds=self._lease_seconds),
                    )
                except BaseException as error:
                    errors.append(error)
                    completed.set()
                    return
                if not renewed:
                    errors.append(LeaseLostError("The work lease was lost."))
                    completed.set()
                    return

        async with anyio.create_task_group() as task_group:
            task_group.start_soon(run_operation)
            task_group.start_soon(renew_lease)
            await completed.wait()
            task_group.cancel_scope.cancel()
        if errors:
            raise errors[0]

    def _retry_at(
        self,
        attempt_count: int,
        retryable: bool,
        now: datetime,
    ) -> datetime | None:
        if not retryable or attempt_count >= self._max_attempts:
            return None
        delay = min(
            self._retry_base_seconds * (2 ** max(attempt_count - 1, 0)),
            3600,
        )
        return now + timedelta(seconds=delay)

    @staticmethod
    def _safe_filename(job: CollectionJobRecord) -> str:
        path_name = PurePosixPath(unquote(urlsplit(job.source_url).path)).name
        return safe_windows_filename(
            job.title or path_name or "document",
            job.expected_document_type,
        )

    @staticmethod
    def _safe_error_detail(error: AcquisitionError) -> str:
        detail = str(error).strip() or "Document acquisition failed."
        return detail[:500]
