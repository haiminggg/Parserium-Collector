from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager, AsyncExitStack
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid4

import anyio
from pydantic import AnyHttpUrl

from parserium_collector.features.acquisition.models import CollectionJobRecord
from parserium_collector.features.acquisition.validation import ValidatedDocument
from parserium_collector.features.analysis.errors import (
    AnalysisCollectionConflictError,
    AnalysisCollectionUnavailableError,
    AnalysisExpiredError,
    AnalysisNotFoundError,
    AnalysisPreviewUnavailableError,
    AnalysisRetryConflictError,
)
from parserium_collector.features.analysis.fingerprint import fingerprint_request
from parserium_collector.features.analysis.job_repository import DiscoveryJobRepository
from parserium_collector.features.analysis.models import (
    PUBLIC_ANALYSIS_STATES,
    AnalysisCandidateResponse,
    AnalysisCollectionRequest,
    AnalysisSessionRecord,
    AnalysisSessionResponse,
    AnalysisSessionStatus,
    CandidateAnalysisRecord,
    CandidateAnalysisStatus,
    CandidateTableRecord,
    CandidateTableResponse,
    DiscoveryCreationReason,
    DiscoverySelection,
    DurableAnalysisSearchRequest,
    PublicAnalysisState,
)
from parserium_collector.features.analysis.repository import AnalysisRepository
from parserium_collector.features.discovery.models import DocumentType
from parserium_collector.features.discovery.scoped import ScopedDiscoveryService
from parserium_collector.features.identity.models import WorkspaceRole, WorkspaceScope
from parserium_collector.features.storage.access import ArtifactAccessService
from parserium_collector.features.storage.errors import ArtifactNotFoundError
from parserium_collector.features.storage.keys import artifact_key
from parserium_collector.features.storage.models import (
    ArtifactKind,
    ArtifactResourceKind,
    ArtifactStream,
    StoredObjectMetadata,
)
from parserium_collector.features.storage.protocols import ArtifactStore
from parserium_collector.features.storage.repository import ArtifactObjectRecord


class AnalysisPromotionValidator(Protocol):
    def validate(self, path: Path, expected_type: DocumentType) -> ValidatedDocument: ...


class AnalysisArtifactRegistry(Protocol):
    async def begin_upload(
        self,
        workspace_id: UUID,
        storage_key: str,
        object_metadata: StoredObjectMetadata,
        *,
        now: datetime,
    ) -> ArtifactObjectRecord: ...

    async def resolve_analysis_object(
        self,
        workspace_id: UUID,
        candidate_analysis_id: UUID,
        kind: ArtifactKind,
    ) -> ArtifactObjectRecord | None: ...


class AnalysisScratchJob(Protocol):
    async def materialize(
        self,
        store: ArtifactStore,
        storage_key: str,
        filename: str,
        *,
        max_bytes: int,
    ) -> Path: ...


class AnalysisScratchStorage(Protocol):
    def job(
        self,
        workspace_id: UUID,
        job_id: UUID,
    ) -> AbstractAsyncContextManager[AnalysisScratchJob]: ...


@dataclass(frozen=True)
class AnalysisService:
    repository: AnalysisRepository
    discovery_service: ScopedDiscoveryService
    session_ttl: timedelta
    session_byte_limit: int
    validator: AnalysisPromotionValidator | None = None
    artifact_repository: AnalysisArtifactRegistry | None = None
    artifact_store: ArtifactStore | None = None
    scratch_storage: AnalysisScratchStorage | None = None
    artifact_access: ArtifactAccessService | None = None
    job_repository: DiscoveryJobRepository | None = None
    fingerprint_secret: bytes | None = None

    def __post_init__(self) -> None:
        if self.session_ttl.total_seconds() <= 0 or self.session_byte_limit < 1:
            raise ValueError("Analysis session limits must be positive.")

    async def start_search(
        self,
        scope: WorkspaceScope,
        request: DurableAnalysisSearchRequest,
        now: datetime,
    ) -> AnalysisSessionResponse:
        if self.job_repository is not None and self.fingerprint_secret is not None:
            selection = await self.discovery_service.prepare(scope, request)
            fingerprint = fingerprint_request(
                self.fingerprint_secret,
                scope.workspace_id,
                request,
                selection,
            )
            reason = (
                DiscoveryCreationReason.FORCE_REFRESH
                if request.force_refresh
                else DiscoveryCreationReason.INITIAL
            )
            submission = await self.job_repository.submit(
                scope,
                request,
                selection,
                fingerprint,
                now,
                now + self.session_ttl,
                self.session_byte_limit,
                reason=reason,
                parent_session_id=None,
            )
            candidates = await self.repository.list_candidate_analyses(
                scope.workspace_id,
                submission.session.id,
            )
            return await self._snapshot(submission.session, candidates)
        discovered = await self.discovery_service.search(scope, request, now)
        session, candidates = await self.repository.create_analysis_session(
            scope,
            request,
            discovered,
            now,
            now + self.session_ttl,
            self.session_byte_limit,
        )
        return await self._snapshot(session, candidates)

    async def get_search(
        self,
        scope: WorkspaceScope,
        session_id: UUID,
        now: datetime,
    ) -> AnalysisSessionResponse:
        session = await self.repository.get_analysis_session(
            scope.workspace_id,
            session_id,
        )
        if session is None:
            raise AnalysisNotFoundError("The analysis search does not exist.")
        if session.status not in {
            AnalysisSessionStatus.QUEUED,
            AnalysisSessionStatus.RUNNING,
        }:
            self._require_unexpired(session.expires_at, now)
        candidates = await self.repository.list_candidate_analyses(
            scope.workspace_id,
            session.id,
        )
        return await self._snapshot(session, candidates)

    async def cancel_search(
        self,
        scope: WorkspaceScope,
        session_id: UUID,
        now: datetime,
    ) -> bool:
        session = await self.repository.get_analysis_session(scope.workspace_id, session_id)
        if session is None or not self._can_manage(scope, session):
            raise AnalysisNotFoundError("The analysis search does not exist.")
        cancelled = await self.repository.cancel_analysis_session(
            scope.workspace_id,
            session_id,
            now,
        )
        if not cancelled:
            raise AnalysisNotFoundError("The analysis search does not exist.")
        return True

    async def retry_search(
        self,
        scope: WorkspaceScope,
        session_id: UUID,
        now: datetime,
    ) -> AnalysisSessionResponse:
        session = await self.repository.get_analysis_session(scope.workspace_id, session_id)
        if session is None or not self._can_manage(scope, session):
            raise AnalysisNotFoundError("The analysis search does not exist.")
        if (
            self.job_repository is None
            or session.status not in {AnalysisSessionStatus.FAILED, AnalysisSessionStatus.CANCELLED}
            or session.request_fingerprint is None
            or session.result_limit is None
        ):
            raise AnalysisRetryConflictError("The analysis search cannot be retried.")
        request = DurableAnalysisSearchRequest(
            query=session.query,
            limit=session.result_limit,
            document_types=session.document_types,
            include_domains=session.include_domains,
            exclude_domains=session.exclude_domains,
            firecrawl_connection_id=session.firecrawl_connection_id,
            tables_required=session.tables_required,
            force_refresh=False,
        )
        selection = DiscoverySelection(
            connection_id=session.firecrawl_connection_id,
            connection_name=session.firecrawl_connection_name_snapshot,
            connection_type=session.firecrawl_connection_type_snapshot,
            credential_revision=session.firecrawl_credential_revision_snapshot,
            provider_identity="",
        )
        try:
            submission = await self.job_repository.submit(
                scope,
                request,
                selection,
                session.request_fingerprint,
                now,
                now + self.session_ttl,
                self.session_byte_limit,
                reason=DiscoveryCreationReason.RETRY,
                parent_session_id=session.id,
            )
        except LookupError as error:
            raise AnalysisRetryConflictError("The analysis search cannot be retried.") from error
        candidates = await self.repository.list_candidate_analyses(
            scope.workspace_id,
            submission.session.id,
        )
        return await self._snapshot(submission.session, candidates)

    async def preview_stream(
        self,
        scope: WorkspaceScope,
        candidate_id: UUID,
        now: datetime,
    ) -> ArtifactStream:
        candidate = await self.repository.get_workspace_candidate_analysis(
            scope.workspace_id,
            candidate_id,
        )
        if candidate is None:
            raise AnalysisNotFoundError("The candidate analysis does not exist.")
        self._require_unexpired(candidate.expires_at, now)
        if self.artifact_access is None:
            raise AnalysisPreviewUnavailableError("The candidate preview is not available.")
        try:
            return await self.artifact_access.open_preview(
                scope.workspace_id,
                candidate.id,
            )
        except ArtifactNotFoundError as error:
            raise AnalysisPreviewUnavailableError(
                "The candidate preview is not available."
            ) from error

    async def collect_candidates(
        self,
        scope: WorkspaceScope,
        request: AnalysisCollectionRequest,
        now: datetime,
    ) -> tuple[CollectionJobRecord, ...]:
        if (
            self.validator is None
            or self.artifact_repository is None
            or self.artifact_store is None
            or self.scratch_storage is None
        ):
            raise AnalysisCollectionUnavailableError("Analysis-based collection is not configured.")
        preflight: list[
            tuple[CandidateAnalysisRecord, ArtifactObjectRecord, Path, UUID, str, str]
        ] = []
        async with AsyncExitStack() as stack:
            for candidate_id in request.analysis_ids:
                candidate = await self.repository.get_workspace_candidate_analysis(
                    scope.workspace_id,
                    candidate_id,
                )
                if candidate is None:
                    raise AnalysisNotFoundError("The candidate analysis does not exist.")
                self._require_unexpired(candidate.expires_at, now)
                if (
                    candidate.status
                    not in {
                        CandidateAnalysisStatus.READY,
                        CandidateAnalysisStatus.NO_TABLES,
                        CandidateAnalysisStatus.PARTIAL,
                    }
                    or candidate.sha256 is None
                    or candidate.media_type is None
                ):
                    raise AnalysisCollectionConflictError(
                        "The candidate analysis is not ready for collection."
                    )
                source_kind = (
                    ArtifactKind.SOURCE_PDF
                    if candidate.document_type is DocumentType.PDF
                    else ArtifactKind.SOURCE_DOCX
                )
                source_object = await self.artifact_repository.resolve_analysis_object(
                    scope.workspace_id,
                    candidate.id,
                    source_kind,
                )
                if source_object is None or source_object.size_bytes is None:
                    raise AnalysisCollectionConflictError(
                        "The candidate source artifact is not available."
                    )
                document_id = uuid4()
                scratch = await stack.enter_async_context(
                    self.scratch_storage.job(scope.workspace_id, document_id)
                )
                source = await scratch.materialize(
                    self.artifact_store,
                    source_object.storage_key,
                    f"source.{candidate.document_type.value}",
                    max_bytes=max(source_object.size_bytes, 1),
                )
                validated = await anyio.to_thread.run_sync(
                    self.validator.validate,
                    source,
                    candidate.document_type,
                )
                if (
                    validated.document_type is not candidate.document_type
                    or validated.media_type != candidate.media_type
                ):
                    raise AnalysisCollectionConflictError(
                        "The candidate document metadata changed before collection."
                    )
                preflight.append(
                    (
                        candidate,
                        source_object,
                        source,
                        document_id,
                        candidate.sha256,
                        candidate.media_type,
                    )
                )

            jobs: list[CollectionJobRecord] = []
            for candidate, source_object, source, document_id, sha256, media_type in preflight:
                if source_object.size_bytes is None:
                    raise AnalysisCollectionConflictError(
                        "The candidate source size is unavailable."
                    )
                storage_key = artifact_key(
                    scope.workspace_id,
                    ArtifactResourceKind.DOCUMENT,
                    document_id,
                    ArtifactKind.STORED_DOCUMENT,
                )
                expected = StoredObjectMetadata(
                    storage_key=storage_key,
                    media_type=media_type,
                    size_bytes=source_object.size_bytes,
                    sha256=sha256,
                )
                artifact_object = await self.artifact_repository.begin_upload(
                    scope.workspace_id,
                    storage_key,
                    expected,
                    now=now,
                )
                stored = await self.artifact_store.put_file(
                    storage_key,
                    source,
                    media_type=expected.media_type,
                    sha256=expected.sha256,
                    size_bytes=expected.size_bytes,
                )
                if stored != expected:
                    raise AnalysisCollectionConflictError(
                        "The promoted artifact metadata did not match."
                    )
                job = await self.repository.promote_candidate_to_collection(
                    scope.workspace_id,
                    candidate.id,
                    document_id=document_id,
                    artifact_object_id=artifact_object.id,
                    size_bytes=stored.size_bytes,
                    now=now,
                )
                if job is None:
                    raise AnalysisCollectionConflictError(
                        "The candidate analysis changed before collection completed."
                    )
                jobs.append(job)
            return tuple(jobs)

    async def _snapshot(
        self,
        session: AnalysisSessionRecord,
        candidates: Sequence[CandidateAnalysisRecord],
    ) -> AnalysisSessionResponse:
        responses: list[AnalysisCandidateResponse] = []
        for candidate in candidates:
            tables = await self.repository.list_candidate_tables(
                candidate.workspace_id,
                candidate.id,
            )
            preview_available = False
            if self.artifact_repository is not None:
                preview_available = (
                    await self.artifact_repository.resolve_analysis_object(
                        candidate.workspace_id,
                        candidate.id,
                        ArtifactKind.PREVIEW_PNG,
                    )
                    is not None
                )
            responses.append(
                self._candidate_response(candidate, tables, preview_available=preview_available)
            )
        return AnalysisSessionResponse(
            id=session.id,
            query=session.query,
            document_types=session.document_types,
            tables_required=session.tables_required,
            firecrawl_connection_id=session.firecrawl_connection_id,
            firecrawl_connection_name_snapshot=session.firecrawl_connection_name_snapshot,
            firecrawl_connection_type_snapshot=session.firecrawl_connection_type_snapshot,
            status=session.status,
            job_stage=session.job_stage,
            error_code=session.error_code,
            candidate_count=session.candidate_count,
            bytes_downloaded=session.bytes_downloaded,
            session_byte_limit=session.session_byte_limit,
            cancellation_requested=session.cancellation_requested,
            created_at=session.created_at,
            updated_at=session.updated_at,
            expires_at=session.expires_at,
            completed_at=session.completed_at,
            candidates=tuple(responses),
        )

    @staticmethod
    def _candidate_response(
        candidate: CandidateAnalysisRecord,
        tables: Sequence[CandidateTableRecord],
        *,
        preview_available: bool,
    ) -> AnalysisCandidateResponse:
        public_state = PUBLIC_ANALYSIS_STATES.get(candidate.status)
        if candidate.status is CandidateAnalysisStatus.PROMOTED:
            public_state = PublicAnalysisState.VALID
        return AnalysisCandidateResponse(
            id=candidate.id,
            ordinal=candidate.ordinal,
            source_url=AnyHttpUrl(candidate.source_url),
            title=candidate.title,
            description=candidate.description,
            document_type=candidate.document_type,
            status=candidate.status,
            public_state=public_state,
            attempt_count=candidate.attempt_count,
            bytes_downloaded=candidate.bytes_downloaded,
            content_length=candidate.content_length,
            page_count=candidate.page_count,
            analyzed_page_count=candidate.analyzed_page_count,
            table_count=candidate.table_count,
            table_count_lower_bound=candidate.table_count_lower_bound,
            preview_available=preview_available,
            preview_page_num=candidate.preview_page_num,
            preview_width=candidate.preview_width,
            preview_height=candidate.preview_height,
            error_code=candidate.error_code,
            error_detail=candidate.error_detail,
            error_retryable=candidate.error_retryable,
            tables=tuple(
                CandidateTableResponse(
                    id=table.id,
                    page_num=table.page_num,
                    table_index=table.table_index,
                    bounding_box=table.bounding_box,
                    cells=table.cells,
                    markdown=table.markdown,
                )
                for table in tables
            ),
            created_at=candidate.created_at,
            updated_at=candidate.updated_at,
            completed_at=candidate.completed_at,
        )

    @staticmethod
    def _require_unexpired(expires_at: datetime, now: datetime) -> None:
        if expires_at <= now:
            raise AnalysisExpiredError("The analysis search has expired.")

    @staticmethod
    def _can_manage(scope: WorkspaceScope, session: AnalysisSessionRecord) -> bool:
        return scope.role is WorkspaceRole.OWNER or (
            scope.user_id is not None and scope.user_id == session.created_by_user_id
        )
