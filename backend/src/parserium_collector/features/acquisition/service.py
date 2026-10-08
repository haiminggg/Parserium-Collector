from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from parserium_collector.features.acquisition.errors import (
    CollectionJobNotFoundError,
    CollectionRetryConflictError,
    DocumentDownloadUnavailableError,
    DocumentNotFoundError,
    ExportUnavailableError,
)
from parserium_collector.features.acquisition.models import (
    CollectionBatchRequest,
    CollectionJobRecord,
    DocumentExportRecord,
    DocumentExportRequest,
    StoredDocumentRecord,
)
from parserium_collector.features.acquisition.pagination import Page, PageCursor
from parserium_collector.features.acquisition.repository import AcquisitionRepository
from parserium_collector.features.identity.models import WorkspaceScope
from parserium_collector.features.storage.access import (
    ArtifactAccessService,
    SignedArtifactDownload,
)
from parserium_collector.features.storage.errors import ArtifactNotFoundError
from parserium_collector.features.storage.models import ArtifactStream


@dataclass(frozen=True)
class DocumentDownload:
    document: StoredDocumentRecord
    artifact: ArtifactStream | SignedArtifactDownload


class AcquisitionService:
    def __init__(
        self,
        repository: AcquisitionRepository,
        *,
        export_available: bool,
        artifact_access: ArtifactAccessService | None = None,
    ) -> None:
        self._repository = repository
        self._export_available = export_available
        self._artifact_access = artifact_access

    async def create_collection(
        self,
        scope: WorkspaceScope,
        request: CollectionBatchRequest,
        now: datetime,
    ) -> tuple[CollectionJobRecord, ...]:
        return await self._repository.create_collection_jobs(
            scope.workspace_id,
            request.candidates,
            now,
            scope.user_id,
        )

    async def list_collection_jobs(
        self,
        scope: WorkspaceScope,
        limit: int,
    ) -> tuple[CollectionJobRecord, ...]:
        return await self._repository.list_collection_jobs(scope.workspace_id, limit)

    async def list_collection_jobs_page(
        self,
        scope: WorkspaceScope,
        limit: int,
        cursor: PageCursor | None,
    ) -> Page[CollectionJobRecord]:
        return await self._repository.list_collection_jobs_page(
            scope.workspace_id,
            limit,
            cursor,
        )

    async def clear_completed_collection_jobs(self, scope: WorkspaceScope) -> int:
        return await self._repository.delete_completed_collection_jobs(scope.workspace_id)

    async def retry_collection(
        self,
        scope: WorkspaceScope,
        job_id: UUID,
        now: datetime,
    ) -> CollectionJobRecord:
        existing = await self._repository.get_collection_job(scope.workspace_id, job_id)
        if existing is None:
            raise CollectionJobNotFoundError("The collection job does not exist.")
        if not await self._repository.retry_collection_job(
            scope.workspace_id,
            job_id,
            now,
        ):
            raise CollectionRetryConflictError("The collection job cannot be retried.")
        retried = await self._repository.get_collection_job(scope.workspace_id, job_id)
        if retried is None:
            raise CollectionJobNotFoundError("The collection job does not exist.")
        return retried

    async def list_documents(
        self,
        scope: WorkspaceScope,
        limit: int,
    ) -> tuple[StoredDocumentRecord, ...]:
        return await self._repository.list_documents(scope.workspace_id, limit)

    async def list_documents_page(
        self,
        scope: WorkspaceScope,
        limit: int,
        cursor: PageCursor | None,
    ) -> Page[StoredDocumentRecord]:
        return await self._repository.list_documents_page(
            scope.workspace_id,
            limit,
            cursor,
        )

    async def download_document(
        self,
        scope: WorkspaceScope,
        document_id: UUID,
    ) -> DocumentDownload:
        document = await self._repository.get_document(scope.workspace_id, document_id)
        if document is None:
            raise DocumentNotFoundError("The document does not exist.")
        if self._artifact_access is None:
            raise DocumentDownloadUnavailableError("Document download storage is not configured.")
        try:
            artifact = await self._artifact_access.open_document(
                scope.workspace_id,
                document_id,
                document.safe_filename,
            )
        except ArtifactNotFoundError as error:
            raise DocumentNotFoundError("The document artifact does not exist.") from error
        return DocumentDownload(document=document, artifact=artifact)

    async def delete_document(
        self,
        scope: WorkspaceScope,
        document_id: UUID,
        now: datetime,
    ) -> None:
        if not await self._repository.delete_document(
            scope.workspace_id,
            document_id,
            now,
        ):
            raise DocumentNotFoundError("The document does not exist.")

    async def create_export(
        self,
        scope: WorkspaceScope,
        document_id: UUID,
        relative_directory: str,
        now: datetime,
    ) -> DocumentExportRecord:
        if not self._export_available:
            raise ExportUnavailableError("Document export is not configured.")
        document = await self._repository.get_document(scope.workspace_id, document_id)
        if document is None:
            raise DocumentNotFoundError("The document does not exist.")
        normalized = DocumentExportRequest(relative_directory=relative_directory).relative_directory
        return await self._repository.create_export(
            scope.workspace_id,
            document.id,
            normalized,
            document.safe_filename,
            now,
            scope.user_id,
        )

    async def list_exports(
        self,
        scope: WorkspaceScope,
        limit: int,
    ) -> tuple[DocumentExportRecord, ...]:
        return await self._repository.list_exports(scope.workspace_id, limit)
