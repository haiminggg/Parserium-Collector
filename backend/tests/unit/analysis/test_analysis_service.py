import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import anyio
import pytest

from parserium_collector.features.acquisition.models import (
    CollectionJobRecord,
    CollectionJobStatus,
)
from parserium_collector.features.acquisition.validation import ValidatedDocument
from parserium_collector.features.analysis.errors import (
    AnalysisCollectionConflictError,
    AnalysisExpiredError,
    AnalysisNotFoundError,
    AnalysisPreviewUnavailableError,
    AnalysisRetryConflictError,
)
from parserium_collector.features.analysis.models import (
    AnalysisCollectionRequest,
    AnalysisSearchRequest,
    AnalysisSessionRecord,
    AnalysisSessionStatus,
    CandidateAnalysisRecord,
    CandidateAnalysisStatus,
    CandidateTableRecord,
    DiscoveryCreationReason,
    DiscoveryJobStage,
    DiscoverySelection,
    DiscoverySubmission,
    DurableAnalysisSearchRequest,
    SubmissionDisposition,
    TableBoundingBox,
)
from parserium_collector.features.analysis.service import AnalysisService
from parserium_collector.features.discovery.models import (
    DocumentCandidate,
    DocumentDiscoveryResponse,
    DocumentType,
)
from parserium_collector.features.discovery.scoped import ScopedDiscoveryResult
from parserium_collector.features.firecrawl_connections.models import ConnectionType
from parserium_collector.features.identity.models import (
    WorkspaceRole,
    WorkspaceScope,
)
from parserium_collector.features.storage.access import ArtifactAccessService
from parserium_collector.features.storage.models import (
    ArtifactKind,
    ArtifactObjectState,
    StoredObjectMetadata,
)
from parserium_collector.features.storage.repository import ArtifactObjectRecord

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
WORKSPACE_A = UUID("10000000-0000-4000-8000-000000000001")
WORKSPACE_B = UUID("10000000-0000-4000-8000-000000000002")
USER_A = UUID("20000000-0000-4000-8000-000000000001")
CONNECTION_A = UUID("30000000-0000-4000-8000-000000000001")
SCOPE_A = WorkspaceScope(WORKSPACE_A, USER_A, WorkspaceRole.OWNER)
SCOPE_B = WorkspaceScope(WORKSPACE_B, USER_A, WorkspaceRole.OWNER)


def session_record(*, expires_at: datetime | None = None) -> AnalysisSessionRecord:
    return AnalysisSessionRecord(
        id=uuid4(),
        workspace_id=WORKSPACE_A,
        created_by_user_id=USER_A,
        firecrawl_connection_id=CONNECTION_A,
        firecrawl_connection_name_snapshot="Workspace cloud",
        firecrawl_connection_type_snapshot=ConnectionType.CLOUD,
        query="bank investment tables",
        document_types=(DocumentType.PDF,),
        include_domains=(),
        exclude_domains=(),
        tables_required=True,
        provider_search_ids=("search-1",),
        status=AnalysisSessionStatus.RUNNING,
        candidate_count=1,
        session_byte_limit=4096,
        bytes_downloaded=1024,
        cancellation_requested=False,
        created_at=NOW,
        updated_at=NOW,
        expires_at=expires_at or NOW + timedelta(hours=1),
        completed_at=None,
        error_code=None,
        error_detail=None,
        request_fingerprint=None,
        request_fingerprint_version=None,
        result_limit=1,
        job_stage=DiscoveryJobStage.ANALYZING,
        cache_reusable_until=None,
        discovery_claimed_by=None,
        discovery_lease_expires_at=None,
        provider_request_started_at=None,
        firecrawl_credential_revision_snapshot=None,
        creation_reason=DiscoveryCreationReason.INITIAL,
    )


def candidate_record(session_id: UUID) -> CandidateAnalysisRecord:
    return CandidateAnalysisRecord(
        id=uuid4(),
        workspace_id=WORKSPACE_A,
        session_id=session_id,
        ordinal=0,
        source_url="https://bank.example/report.pdf",
        title="Annual report",
        description="Investment tables",
        document_type=DocumentType.PDF,
        status=CandidateAnalysisStatus.READY,
        attempt_count=1,
        available_at=NOW,
        claimed_by=None,
        lease_expires_at=None,
        bytes_downloaded=1024,
        content_length=1024,
        sha256="b" * 64,
        media_type="application/pdf",
        safe_filename="Annual report.pdf",
        page_count=4,
        analyzed_page_count=4,
        table_count=1,
        table_count_lower_bound=False,
        preview_page_num=2,
        preview_width=1275,
        preview_height=1650,
        error_code=None,
        error_detail=None,
        error_retryable=None,
        promoted_document_id=None,
        created_at=NOW,
        updated_at=NOW,
        started_at=NOW,
        completed_at=NOW,
        expires_at=NOW + timedelta(hours=1),
    )


class DiscoveryServiceTestDouble:
    async def search(
        self,
        scope: WorkspaceScope,
        request: Any,
        now: datetime,
    ) -> ScopedDiscoveryResult:
        assert scope == SCOPE_A
        assert now == NOW
        return ScopedDiscoveryResult(
            response=DocumentDiscoveryResponse(
                provider_search_ids=["search-1"],
                candidates=[
                    DocumentCandidate(
                        url="https://bank.example/report.pdf",
                        title="Annual report",
                        description="Investment tables",
                        document_type="pdf",
                    )
                ],
                rejected_non_document_results=2,
            ),
            connection_id=CONNECTION_A,
            connection_name_snapshot="Workspace cloud",
            connection_type_snapshot=ConnectionType.CLOUD,
        )


class AnalysisRepositoryTestDouble:
    def __init__(
        self,
        session: AnalysisSessionRecord,
        candidate: CandidateAnalysisRecord,
    ) -> None:
        self.session = session
        self.candidate = candidate
        self.created: dict[str, Any] | None = None
        self.cancelled = False
        self.promotion_conflict = False

    async def create_analysis_session(self, *args: Any, **kwargs: Any):
        self.created = {"args": args, "kwargs": kwargs}
        return self.session, (self.candidate,)

    async def get_analysis_session(
        self,
        workspace_id: UUID,
        session_id: UUID,
    ) -> AnalysisSessionRecord | None:
        if session_id != self.session.id or workspace_id != WORKSPACE_A:
            return None
        return self.session

    async def list_candidate_analyses(
        self,
        workspace_id: UUID,
        session_id: UUID,
    ) -> tuple[CandidateAnalysisRecord, ...]:
        if workspace_id != WORKSPACE_A or session_id != self.session.id:
            return ()
        return (self.candidate,)

    async def get_workspace_candidate_analysis(
        self,
        workspace_id: UUID,
        candidate_id: UUID,
    ) -> CandidateAnalysisRecord | None:
        if candidate_id != self.candidate.id or workspace_id != WORKSPACE_A:
            return None
        return self.candidate

    async def list_candidate_tables(
        self,
        workspace_id: UUID,
        candidate_id: UUID,
    ) -> tuple[CandidateTableRecord, ...]:
        if workspace_id != WORKSPACE_A or candidate_id != self.candidate.id:
            return ()
        return (
            CandidateTableRecord(
                id=uuid4(),
                workspace_id=WORKSPACE_A,
                candidate_analysis_id=candidate_id,
                page_num=2,
                table_index=0,
                bounding_box=TableBoundingBox(x=10, y=20, width=200, height=80),
                cells=(("Fund", "NAV"), ("Alpha", "$42")),
                markdown="| Fund | NAV |\n| --- | --- |\n| Alpha | $42 |",
                created_at=NOW,
            ),
        )

    async def cancel_analysis_session(
        self,
        workspace_id: UUID,
        session_id: UUID,
        now: datetime,
    ) -> bool:
        if session_id != self.session.id or workspace_id != WORKSPACE_A:
            return False
        self.cancelled = True
        return True

    async def promote_candidate_to_collection(
        self,
        workspace_id: UUID,
        candidate_id: UUID,
        *,
        document_id: UUID,
        artifact_object_id: UUID,
        size_bytes: int,
        now: datetime,
    ) -> CollectionJobRecord | None:
        if self.promotion_conflict:
            return None
        return CollectionJobRecord(
            id=uuid4(),
            workspace_id=WORKSPACE_A,
            source_url=self.candidate.source_url,
            title=self.candidate.title,
            expected_document_type=self.candidate.document_type,
            status=CollectionJobStatus.COMPLETED,
            attempt_count=0,
            available_at=now,
            claimed_by=None,
            lease_expires_at=None,
            bytes_downloaded=size_bytes,
            content_length=size_bytes,
            document_id=document_id,
            error_code=None,
            error_detail=None,
            error_retryable=None,
            created_at=now,
            updated_at=now,
            started_at=now,
            completed_at=now,
        )


class DiscoveryJobRepositoryTestDouble:
    def __init__(self, retried: AnalysisSessionRecord) -> None:
        self.retried = retried
        self.submitted: dict[str, Any] | None = None

    async def submit(self, *args: Any, **kwargs: Any) -> DiscoverySubmission:
        self.submitted = {"args": args, "kwargs": kwargs}
        return DiscoverySubmission(self.retried, SubmissionDisposition.CREATED)


def artifact_record(
    workspace_id: UUID,
    object_metadata: StoredObjectMetadata,
    *,
    state: ArtifactObjectState = ArtifactObjectState.AVAILABLE,
) -> ArtifactObjectRecord:
    return ArtifactObjectRecord(
        id=uuid4(),
        workspace_id=workspace_id,
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


class ArtifactRegistryTestDouble:
    def __init__(
        self,
        candidate: CandidateAnalysisRecord,
        source: Path,
        *,
        preview_available: bool,
    ) -> None:
        source_metadata = StoredObjectMetadata(
            storage_key="analysis/private/source.pdf",
            media_type="application/pdf",
            size_bytes=source.stat().st_size,
            sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        )
        preview_metadata = StoredObjectMetadata(
            storage_key="analysis/private/preview.png",
            media_type="image/png",
            size_bytes=len(b"\x89PNG\r\n\x1a\ntest-only preview"),
            sha256="a" * 64,
        )
        self.source = artifact_record(candidate.workspace_id, source_metadata)
        self.preview = (
            artifact_record(candidate.workspace_id, preview_metadata) if preview_available else None
        )
        self.uploads: list[ArtifactObjectRecord] = []

    async def begin_upload(
        self,
        workspace_id: UUID,
        storage_key: str,
        object_metadata: StoredObjectMetadata,
        *,
        now: datetime,
    ) -> ArtifactObjectRecord:
        assert storage_key == object_metadata.storage_key
        record = artifact_record(
            workspace_id,
            object_metadata,
            state=ArtifactObjectState.UPLOADING,
        )
        self.uploads.append(record)
        return record

    async def resolve_analysis_object(
        self,
        workspace_id: UUID,
        candidate_analysis_id: UUID,
        kind: ArtifactKind,
    ) -> ArtifactObjectRecord | None:
        assert workspace_id == WORKSPACE_A
        if kind is ArtifactKind.SOURCE_PDF:
            return self.source
        if kind is ArtifactKind.PREVIEW_PNG:
            return self.preview
        return None


class ArtifactStoreTestDouble:
    def __init__(self) -> None:
        self.uploads: list[StoredObjectMetadata] = []

    async def put_file(
        self,
        storage_key: str,
        source: Path,
        *,
        media_type: str,
        sha256: str,
        size_bytes: int,
    ) -> StoredObjectMetadata:
        assert await anyio.Path(source).is_file()
        metadata = StoredObjectMetadata(
            storage_key=storage_key,
            media_type=media_type,
            size_bytes=size_bytes,
            sha256=sha256,
        )
        self.uploads.append(metadata)
        return metadata


class ScratchJobTestDouble:
    def __init__(self, source: Path) -> None:
        self.source = source

    async def materialize(
        self,
        store: ArtifactStoreTestDouble,
        storage_key: str,
        filename: str,
        *,
        max_bytes: int,
    ) -> Path:
        assert storage_key == "analysis/private/source.pdf"
        assert filename == "source.pdf"
        assert max_bytes >= self.source.stat().st_size
        return self.source


class ScratchStorageTestDouble:
    def __init__(self, source: Path) -> None:
        self.source = source
        self.cleaned = False

    @asynccontextmanager
    async def job(
        self,
        workspace_id: UUID,
        job_id: UUID,
    ) -> AsyncIterator[ScratchJobTestDouble]:
        assert workspace_id == WORKSPACE_A
        try:
            yield ScratchJobTestDouble(self.source)
        finally:
            self.cleaned = True


class ValidatorTestDouble:
    def validate(self, path: Path, expected_type: DocumentType) -> ValidatedDocument:
        assert path.read_bytes().startswith(b"%PDF-")
        return ValidatedDocument(
            document_type=expected_type,
            media_type="application/pdf",
        )


def service_for(
    tmp_path: Path,
    *,
    session: AnalysisSessionRecord | None = None,
    candidate: CandidateAnalysisRecord | None = None,
    preview_available: bool = True,
) -> tuple[AnalysisService, AnalysisRepositoryTestDouble, Path]:
    resolved_session = session or session_record()
    resolved_candidate = candidate or candidate_record(resolved_session.id)
    repository = AnalysisRepositoryTestDouble(resolved_session, resolved_candidate)
    preview = tmp_path / "preview.png"
    preview.write_bytes(b"\x89PNG\r\n\x1a\ntest-only preview")
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-1.7\ntest-only source")
    resolved_candidate = replace(
        resolved_candidate,
        sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
    )
    repository.candidate = resolved_candidate
    registry = ArtifactRegistryTestDouble(
        resolved_candidate,
        source,
        preview_available=preview_available,
    )
    store = ArtifactStoreTestDouble()
    return (
        AnalysisService(
            repository=repository,
            discovery_service=DiscoveryServiceTestDouble(),
            session_ttl=timedelta(hours=1),
            session_byte_limit=4096,
            validator=ValidatorTestDouble(),
            artifact_repository=registry,
            artifact_store=store,
            scratch_storage=ScratchStorageTestDouble(source),
            artifact_access=ArtifactAccessService(
                registry,
                store,
                signed_url_ttl_seconds=60,
            ),
        ),
        repository,
        preview,
    )


async def test_start_search_persists_provider_candidates_and_returns_snapshot(
    tmp_path: Path,
) -> None:
    service, repository, _ = service_for(tmp_path)
    request = AnalysisSearchRequest(
        query="bank investment tables",
        document_types=("pdf",),
        tables_required=True,
        firecrawl_connection_id=CONNECTION_A,
    )

    snapshot = await service.start_search(SCOPE_A, request, NOW)

    assert repository.created is not None
    assert repository.created["args"][0] == SCOPE_A
    assert repository.created["args"][2].response.provider_search_ids == ["search-1"]
    assert repository.created["args"][2].connection_id == CONNECTION_A
    assert repository.created["args"][4] == NOW + timedelta(hours=1)
    assert repository.created["args"][5] == 4096
    assert snapshot.candidates[0].public_state == "valid"
    assert snapshot.firecrawl_connection_id == CONNECTION_A
    assert snapshot.firecrawl_connection_name_snapshot == "Workspace cloud"
    assert snapshot.firecrawl_connection_type_snapshot is ConnectionType.CLOUD
    assert snapshot.candidates[0].tables[0].cells[1] == ("Alpha", "$42")
    assert "storage" not in str(snapshot.model_dump())


async def test_owner_scoping_expiry_and_cancellation_are_enforced(tmp_path: Path) -> None:
    service, repository, _ = service_for(tmp_path)

    with pytest.raises(AnalysisNotFoundError):
        await service.get_search(SCOPE_B, repository.session.id, NOW)
    expired = replace(
        repository.session,
        status=AnalysisSessionStatus.COMPLETED,
        job_stage=DiscoveryJobStage.COMPLETED,
        expires_at=NOW,
        completed_at=NOW,
    )
    expired_service, _, _ = service_for(tmp_path, session=expired)
    with pytest.raises(AnalysisExpiredError):
        await expired_service.get_search(SCOPE_A, expired.id, NOW)
    assert await service.cancel_search(SCOPE_A, repository.session.id, NOW) is True
    assert repository.cancelled is True


async def test_active_search_remains_visible_past_its_initial_expiry(tmp_path: Path) -> None:
    repository_session = session_record()
    expired_active = replace(repository_session, expires_at=NOW)
    service, _, _ = service_for(tmp_path, session=expired_active)

    snapshot = await service.get_search(SCOPE_A, repository_session.id, NOW)

    assert snapshot.status is AnalysisSessionStatus.RUNNING


async def test_failed_durable_discovery_can_be_retried_as_a_linked_job(tmp_path: Path) -> None:
    parent = replace(
        session_record(),
        status=AnalysisSessionStatus.FAILED,
        job_stage=DiscoveryJobStage.FAILED,
        request_fingerprint="a" * 64,
        request_fingerprint_version=1,
        result_limit=12,
        error_code="provider_timeout",
        completed_at=NOW,
    )
    service, repository, _ = service_for(tmp_path, session=parent)
    retried = replace(
        parent,
        id=uuid4(),
        status=AnalysisSessionStatus.QUEUED,
        job_stage=DiscoveryJobStage.QUEUED,
        creation_reason=DiscoveryCreationReason.RETRY,
        completed_at=None,
        error_code=None,
    )
    jobs = DiscoveryJobRepositoryTestDouble(retried)
    service = replace(service, job_repository=jobs, fingerprint_secret=b"unused")

    response = await service.retry_search(SCOPE_A, parent.id, NOW)

    assert response.id == retried.id
    assert jobs.submitted is not None
    request = jobs.submitted["args"][1]
    selection = jobs.submitted["args"][2]
    assert isinstance(request, DurableAnalysisSearchRequest)
    assert request.limit == 12
    assert isinstance(selection, DiscoverySelection)
    assert jobs.submitted["args"][3] == "a" * 64
    assert jobs.submitted["kwargs"] == {
        "reason": DiscoveryCreationReason.RETRY,
        "parent_session_id": parent.id,
    }


async def test_retry_rejects_non_terminal_or_legacy_discovery(tmp_path: Path) -> None:
    service, repository, _ = service_for(tmp_path)
    retried = replace(repository.session, id=uuid4())
    service = replace(
        service,
        job_repository=DiscoveryJobRepositoryTestDouble(retried),
        fingerprint_secret=b"unused",
    )

    with pytest.raises(AnalysisRetryConflictError):
        await service.retry_search(SCOPE_A, repository.session.id, NOW)


async def test_preview_is_owner_scoped_terminal_and_resolved_by_registry(
    tmp_path: Path,
) -> None:
    service, repository, _ = service_for(tmp_path)

    preview = await service.preview_stream(SCOPE_A, repository.candidate.id, NOW)
    assert preview.media_type == "image/png"
    assert preview.size_bytes == len(b"\x89PNG\r\n\x1a\ntest-only preview")
    with pytest.raises(AnalysisNotFoundError):
        await service.preview_stream(SCOPE_B, repository.candidate.id, NOW)
    unavailable = repository.candidate
    unavailable_service, _, _ = service_for(
        tmp_path,
        candidate=unavailable,
        preview_available=False,
    )
    with pytest.raises(AnalysisPreviewUnavailableError):
        await unavailable_service.preview_stream(SCOPE_A, unavailable.id, NOW)


async def test_collection_promotes_validated_bytes_without_network_download(
    tmp_path: Path,
) -> None:
    service, repository, _ = service_for(tmp_path)
    request = AnalysisCollectionRequest(analysis_ids=(repository.candidate.id,))

    jobs = await service.collect_candidates(SCOPE_A, request, NOW)

    assert len(jobs) == 1
    assert jobs[0].status is CollectionJobStatus.COMPLETED
    assert jobs[0].bytes_downloaded == len(b"%PDF-1.7\ntest-only source")
    assert isinstance(service.scratch_storage, ScratchStorageTestDouble)
    assert service.scratch_storage.cleaned is True
    assert isinstance(service.artifact_repository, ArtifactRegistryTestDouble)
    assert len(service.artifact_repository.uploads) == 1
    assert isinstance(service.artifact_store, ArtifactStoreTestDouble)
    assert len(service.artifact_store.uploads) == 1

    repository.promotion_conflict = True
    with pytest.raises(AnalysisCollectionConflictError):
        await service.collect_candidates(SCOPE_A, request, NOW)
