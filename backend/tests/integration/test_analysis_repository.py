import hashlib
import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from anyio import Path
from sqlalchemy import URL, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from parserium_collector.adapters.database.tables import (
    artifact_objects,
    artifact_references,
    candidate_analyses,
    candidate_tables,
    discovery_analysis_sessions,
    documents,
    local_sessions,
    metadata,
    workspaces,
)
from parserium_collector.features.analysis.models import (
    AnalysisSearchRequest,
    CandidateAnalysisStatus,
    CandidateTableInput,
    TableBoundingBox,
)
from parserium_collector.features.analysis.repository import PostgresAnalysisRepository
from parserium_collector.features.discovery.models import (
    DocumentCandidate,
    DocumentDiscoveryResponse,
)
from parserium_collector.features.discovery.scoped import ScopedDiscoveryResult
from parserium_collector.features.identity.models import WorkspaceRole, WorkspaceScope
from parserium_collector.features.storage.keys import artifact_key
from parserium_collector.features.storage.models import (
    ArtifactKind,
    ArtifactObjectState,
    ArtifactResourceKind,
    StoredObjectMetadata,
)
from parserium_collector.features.storage.repository import (
    ArtifactObjectRecord,
    PostgresArtifactRepository,
)

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
OWNER_DIGEST = "a" * 64
WORKSPACE_A = UUID("10000000-0000-4000-8000-000000000001")
WORKSPACE_B = UUID("10000000-0000-4000-8000-000000000002")
SCOPE_A = WorkspaceScope(WORKSPACE_A, None, WorkspaceRole.OWNER)


@pytest.fixture
async def database_engine() -> AsyncEngine:
    password_file = Path(os.environ["TEST_DATABASE_PASSWORD_FILE"])
    database_url = URL.create(
        "postgresql+psycopg",
        username="parserium_collector",
        password=(await password_file.read_text(encoding="utf-8")).strip(),
        host=os.environ["TEST_DATABASE_HOST"],
        port=5432,
        database="parserium_collector",
    ).render_as_string(hide_password=False)
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(metadata.drop_all)
        await connection.run_sync(metadata.create_all)
        await connection.execute(
            insert(workspaces),
            [
                {
                    "id": WORKSPACE_A,
                    "name": "Workspace A",
                    "is_local": True,
                    "created_at": NOW,
                    "updated_at": NOW,
                },
                {
                    "id": WORKSPACE_B,
                    "name": "Workspace B",
                    "is_local": False,
                    "created_at": NOW,
                    "updated_at": NOW,
                },
            ],
        )
        await connection.execute(
            insert(local_sessions).values(
                token_digest=OWNER_DIGEST,
                workspace_id=WORKSPACE_A,
                created_at=NOW,
                last_seen_at=NOW,
                revoked_at=None,
            )
        )
    try:
        yield engine
    finally:
        await engine.dispose()


def candidate(index: int) -> DocumentCandidate:
    return DocumentCandidate(
        url=f"https://documents.example/report-{index}.pdf",
        title=f"Report {index}",
        description=f"Candidate {index}",
        document_type="pdf",
    )


async def create_session(
    repository: PostgresAnalysisRepository,
    *candidates: DocumentCandidate,
    byte_limit: int = 512 * 1024 * 1024,
):
    return await repository.create_analysis_session(
        SCOPE_A,
        AnalysisSearchRequest(query="investment tables", limit=len(candidates)),
        ScopedDiscoveryResult(
            response=DocumentDiscoveryResponse(
                provider_search_ids=["provider-search"],
                candidates=list(candidates),
                rejected_non_document_results=0,
            ),
            connection_id=None,
            connection_name_snapshot=None,
            connection_type_snapshot=None,
        ),
        NOW,
        NOW + timedelta(hours=1),
        byte_limit,
    )


async def begin_artifact(
    repository: PostgresArtifactRepository,
    *,
    workspace_id: UUID,
    resource_kind: ArtifactResourceKind,
    resource_id: UUID,
    kind: ArtifactKind,
    size_bytes: int = 1024,
    sha256: str | None = None,
) -> ArtifactObjectRecord:
    storage_key = artifact_key(workspace_id, resource_kind, resource_id, kind)
    media_type = "image/png" if kind is ArtifactKind.PREVIEW_PNG else "application/pdf"
    return await repository.begin_upload(
        workspace_id,
        storage_key,
        StoredObjectMetadata(
            storage_key=storage_key,
            media_type=media_type,
            size_bytes=size_bytes,
            sha256=sha256 or hashlib.sha256(storage_key.encode()).hexdigest(),
        ),
        now=NOW,
    )


async def test_candidate_claim_is_exclusive_and_expired_lease_recovers(
    database_engine: AsyncEngine,
) -> None:
    artifacts = PostgresArtifactRepository(database_engine)
    repository = PostgresAnalysisRepository(database_engine, artifacts)
    session, created = await create_session(repository, candidate(1))

    claimed = await repository.claim_candidate_analysis(
        "worker-a", NOW, NOW + timedelta(seconds=60)
    )
    unavailable = await repository.claim_candidate_analysis(
        "worker-b", NOW + timedelta(seconds=30), NOW + timedelta(seconds=90)
    )
    recovered = await repository.claim_candidate_analysis(
        "worker-b", NOW + timedelta(seconds=61), NOW + timedelta(seconds=121)
    )

    async with database_engine.connect() as connection:
        durable_state = (
            await connection.execute(
                select(
                    discovery_analysis_sessions.c.status,
                    discovery_analysis_sessions.c.job_stage,
                    discovery_analysis_sessions.c.creation_reason,
                ).where(discovery_analysis_sessions.c.id == session.id)
            )
        ).one()

    assert session.candidate_count == 1
    assert session.request_fingerprint is None
    assert session.request_fingerprint_version is None
    assert session.result_limit == 1
    assert session.job_stage.value == "analyzing"
    assert session.cache_reusable_until is None
    assert session.discovery_claimed_by is None
    assert session.discovery_lease_expires_at is None
    assert session.provider_request_started_at is None
    assert session.firecrawl_credential_revision_snapshot is None
    assert session.creation_reason.value == "initial"
    assert created[0].ordinal == 0
    assert claimed is not None
    assert claimed.id == created[0].id
    assert claimed.status is CandidateAnalysisStatus.DOWNLOADING
    assert claimed.claimed_by == "worker-a"
    assert unavailable is None
    assert recovered is not None
    assert recovered.id == claimed.id
    assert recovered.claimed_by == "worker-b"
    assert recovered.attempt_count == 2
    assert durable_state == ("running", "analyzing", "initial")


async def test_progress_stages_and_tables_complete_transactionally(
    database_engine: AsyncEngine,
) -> None:
    artifacts = PostgresArtifactRepository(database_engine)
    repository = PostgresAnalysisRepository(database_engine, artifacts)
    session, _ = await create_session(repository, candidate(1))
    claimed = await repository.claim_candidate_analysis(
        "worker-a", NOW, NOW + timedelta(seconds=60)
    )
    assert claimed is not None

    assert (
        await repository.update_candidate_progress(
            claimed.id,
            "worker-a",
            512,
            1024,
            NOW,
        )
        is True
    )
    assert (
        await repository.transition_candidate_stage(
            claimed.id,
            "worker-a",
            CandidateAnalysisStatus.DOWNLOADING,
            CandidateAnalysisStatus.PARSING,
            NOW,
        )
        is False
    )
    assert await repository.transition_candidate_stage(
        claimed.id,
        "worker-a",
        CandidateAnalysisStatus.DOWNLOADING,
        CandidateAnalysisStatus.VALIDATING,
        NOW,
    )
    assert await repository.transition_candidate_stage(
        claimed.id,
        "worker-a",
        CandidateAnalysisStatus.VALIDATING,
        CandidateAnalysisStatus.PARSING,
        NOW,
    )
    table = CandidateTableInput(
        page_num=1,
        table_index=0,
        bounding_box=TableBoundingBox(x=10, y=20, width=300, height=100),
        cells=(("Fund", "NAV"), ("A", "$10")),
        markdown="| Fund | NAV |\n| --- | --- |\n| A | $10 |",
    )
    source = await begin_artifact(
        artifacts,
        workspace_id=WORKSPACE_A,
        resource_kind=ArtifactResourceKind.ANALYSIS,
        resource_id=claimed.id,
        kind=ArtifactKind.SOURCE_PDF,
    )
    preview = await begin_artifact(
        artifacts,
        workspace_id=WORKSPACE_A,
        resource_kind=ArtifactResourceKind.ANALYSIS,
        resource_id=claimed.id,
        kind=ArtifactKind.PREVIEW_PNG,
    )

    completed = await repository.complete_candidate_analysis(
        claimed.id,
        "worker-a",
        status=CandidateAnalysisStatus.READY,
        sha256="b" * 64,
        media_type="application/pdf",
        safe_filename="report.pdf",
        artifacts=(
            (source.id, ArtifactKind.SOURCE_PDF),
            (preview.id, ArtifactKind.PREVIEW_PNG),
        ),
        page_count=2,
        analyzed_page_count=2,
        table_count_lower_bound=False,
        preview_page_num=1,
        preview_width=1275,
        preview_height=1650,
        tables=(table,),
        now=NOW,
    )

    stored_tables = await repository.list_candidate_tables(WORKSPACE_A, completed.id)
    progress = await repository.get_analysis_progress(WORKSPACE_A, session.id)
    stored_source = await artifacts.resolve_analysis_object(
        WORKSPACE_A,
        completed.id,
        ArtifactKind.SOURCE_PDF,
    )
    stored_preview = await artifacts.resolve_analysis_object(
        WORKSPACE_A,
        completed.id,
        ArtifactKind.PREVIEW_PNG,
    )
    usage = await artifacts.get_usage(WORKSPACE_A)
    assert completed.status is CandidateAnalysisStatus.READY
    assert completed.table_count == 1
    assert len(stored_tables) == 1
    assert stored_tables[0].cells == table.cells
    assert progress is not None
    assert progress.bytes_downloaded == 512
    assert progress.ready_count == 1
    assert progress.terminal_count == 1
    assert stored_source is not None
    assert stored_source.state is ArtifactObjectState.AVAILABLE
    assert stored_preview is not None
    assert usage.retained_bytes == 2048
    assert usage.retained_objects == 2


async def test_live_lease_renewal_and_session_byte_budget_are_enforced(
    database_engine: AsyncEngine,
) -> None:
    artifacts = PostgresArtifactRepository(database_engine)
    repository = PostgresAnalysisRepository(database_engine, artifacts)
    session, _ = await create_session(repository, candidate(1), byte_limit=1024)
    claimed = await repository.claim_candidate_analysis(
        "worker-a", NOW, NOW + timedelta(seconds=60)
    )
    assert claimed is not None

    assert not await repository.renew_candidate_lease(
        claimed.id,
        "worker-b",
        NOW + timedelta(seconds=30),
        NOW + timedelta(seconds=120),
    )
    assert await repository.renew_candidate_lease(
        claimed.id,
        "worker-a",
        NOW + timedelta(seconds=30),
        NOW + timedelta(seconds=120),
    )
    assert await repository.update_candidate_progress(
        claimed.id,
        "worker-a",
        768,
        2048,
        NOW,
    )
    assert not await repository.update_candidate_progress(
        claimed.id,
        "worker-a",
        512,
        2048,
        NOW,
    )
    assert not await repository.update_candidate_progress(
        claimed.id,
        "worker-a",
        1025,
        2048,
        NOW,
    )
    progress = await repository.get_analysis_progress(WORKSPACE_A, session.id)
    assert progress is not None
    assert progress.bytes_downloaded == 768

    unavailable = await repository.claim_candidate_analysis(
        "worker-b",
        NOW + timedelta(seconds=61),
        NOW + timedelta(seconds=121),
    )
    recovered = await repository.claim_candidate_analysis(
        "worker-b",
        NOW + timedelta(seconds=121),
        NOW + timedelta(seconds=181),
    )
    assert unavailable is None
    assert recovered is not None
    assert recovered.id == claimed.id


async def test_retry_failure_and_cancellation_are_owned_and_idempotent(
    database_engine: AsyncEngine,
) -> None:
    artifacts = PostgresArtifactRepository(database_engine)
    repository = PostgresAnalysisRepository(database_engine, artifacts)
    session, created = await create_session(repository, candidate(1), candidate(2))
    first = await repository.claim_candidate_analysis("worker-a", NOW, NOW + timedelta(seconds=60))
    assert first is not None
    retry_at = NOW + timedelta(seconds=30)
    assert await repository.fail_candidate_analysis(
        first.id,
        "worker-a",
        error_code="download_timeout",
        error_detail="The document server timed out.",
        retryable=True,
        retry_at=retry_at,
        now=NOW,
    )
    retried = await repository.claim_candidate_analysis(
        "worker-b", retry_at, retry_at + timedelta(seconds=60)
    )
    assert retried is not None
    assert retried.id == first.id
    assert await repository.fail_candidate_analysis(
        retried.id,
        "worker-b",
        error_code="blocked_destination",
        error_detail="The destination is not allowed.",
        retryable=False,
        retry_at=None,
        now=retry_at,
    )
    failed = await repository.get_workspace_candidate_analysis(WORKSPACE_A, retried.id)
    assert failed is not None
    assert failed.status is CandidateAnalysisStatus.FAILED
    assert failed.expires_at == session.expires_at

    assert await repository.cancel_analysis_session(WORKSPACE_A, session.id, retry_at)
    assert await repository.cancel_analysis_session(WORKSPACE_A, session.id, retry_at)
    assert not await repository.cancel_analysis_session(
        WORKSPACE_B,
        session.id,
        retry_at,
    )
    records = await repository.list_candidate_analyses(WORKSPACE_A, session.id)
    assert {record.id for record in records} == {record.id for record in created}
    assert {record.status for record in records} == {
        CandidateAnalysisStatus.FAILED,
        CandidateAnalysisStatus.CANCELLED,
    }
    assert {record.expires_at for record in records} == {retry_at}
    async with database_engine.connect() as connection:
        assert (
            await connection.scalar(
                select(discovery_analysis_sessions.c.job_stage).where(
                    discovery_analysis_sessions.c.id == session.id
                )
            )
            == "cancelled"
        )


async def test_cancellation_immediately_expires_completed_artifact_references(
    database_engine: AsyncEngine,
) -> None:
    artifacts = PostgresArtifactRepository(database_engine)
    repository = PostgresAnalysisRepository(database_engine, artifacts)
    session, _ = await create_session(repository, candidate(1), candidate(2))
    claimed = await repository.claim_candidate_analysis(
        "worker-a",
        NOW,
        NOW + timedelta(seconds=60),
    )
    assert claimed is not None
    assert await repository.transition_candidate_stage(
        claimed.id,
        "worker-a",
        CandidateAnalysisStatus.DOWNLOADING,
        CandidateAnalysisStatus.VALIDATING,
        NOW,
    )
    assert await repository.transition_candidate_stage(
        claimed.id,
        "worker-a",
        CandidateAnalysisStatus.VALIDATING,
        CandidateAnalysisStatus.PARSING,
        NOW,
    )
    source = await begin_artifact(
        artifacts,
        workspace_id=WORKSPACE_A,
        resource_kind=ArtifactResourceKind.ANALYSIS,
        resource_id=claimed.id,
        kind=ArtifactKind.SOURCE_PDF,
    )
    await repository.complete_candidate_analysis(
        claimed.id,
        "worker-a",
        status=CandidateAnalysisStatus.READY,
        sha256="f" * 64,
        media_type="application/pdf",
        safe_filename="cancelled.pdf",
        artifacts=((source.id, ArtifactKind.SOURCE_PDF),),
        page_count=1,
        analyzed_page_count=1,
        table_count_lower_bound=False,
        preview_page_num=None,
        preview_width=None,
        preview_height=None,
        tables=(),
        now=NOW,
    )
    cancelled_at = NOW + timedelta(seconds=10)

    assert await repository.cancel_analysis_session(
        WORKSPACE_A,
        session.id,
        cancelled_at,
    )

    records = await repository.list_candidate_analyses(WORKSPACE_A, session.id)
    async with database_engine.connect() as connection:
        reference_expiration = await connection.scalar(
            select(artifact_references.c.expires_at).where(
                artifact_references.c.artifact_object_id == source.id,
                artifact_references.c.removed_at.is_(None),
            )
        )
    assert {record.expires_at for record in records} == {cancelled_at}
    assert reference_expiration == cancelled_at


async def test_completion_replaces_staged_tables_and_promotion_is_compare_and_set(
    database_engine: AsyncEngine,
) -> None:
    artifacts = PostgresArtifactRepository(database_engine)
    repository = PostgresAnalysisRepository(database_engine, artifacts)
    session, _ = await create_session(repository, candidate(1))
    claimed = await repository.claim_candidate_analysis(
        "worker-a", NOW, NOW + timedelta(seconds=60)
    )
    assert claimed is not None
    assert await repository.transition_candidate_stage(
        claimed.id,
        "worker-a",
        CandidateAnalysisStatus.DOWNLOADING,
        CandidateAnalysisStatus.VALIDATING,
        NOW,
    )
    assert await repository.transition_candidate_stage(
        claimed.id,
        "worker-a",
        CandidateAnalysisStatus.VALIDATING,
        CandidateAnalysisStatus.PARSING,
        NOW,
    )
    async with database_engine.begin() as connection:
        await connection.execute(
            insert(candidate_tables).values(
                id=uuid4(),
                workspace_id=WORKSPACE_A,
                candidate_analysis_id=claimed.id,
                page_num=1,
                table_index=99,
                x=0,
                y=0,
                width=1,
                height=1,
                cells=[["stale"]],
                markdown="| stale |",
                created_at=NOW,
            )
        )
    replacement = CandidateTableInput(
        page_num=2,
        table_index=0,
        bounding_box=TableBoundingBox(x=5, y=6, width=100, height=40),
        cells=(("Current",),),
        markdown="| Current |\n| --- |",
    )
    source = await begin_artifact(
        artifacts,
        workspace_id=WORKSPACE_A,
        resource_kind=ArtifactResourceKind.ANALYSIS,
        resource_id=claimed.id,
        kind=ArtifactKind.SOURCE_PDF,
    )
    completed = await repository.complete_candidate_analysis(
        claimed.id,
        "worker-a",
        status=CandidateAnalysisStatus.READY,
        sha256="c" * 64,
        media_type="application/pdf",
        safe_filename="current.pdf",
        artifacts=((source.id, ArtifactKind.SOURCE_PDF),),
        page_count=2,
        analyzed_page_count=2,
        table_count_lower_bound=False,
        preview_page_num=None,
        preview_width=None,
        preview_height=None,
        tables=(replacement,),
        now=NOW,
    )
    stored_tables = await repository.list_candidate_tables(WORKSPACE_A, completed.id)
    assert [(table.page_num, table.table_index) for table in stored_tables] == [(2, 0)]

    document_id = uuid4()
    async with database_engine.begin() as connection:
        await connection.execute(
            insert(documents).values(
                id=document_id,
                workspace_id=WORKSPACE_A,
                sha256="d" * 64,
                document_type="pdf",
                media_type="application/pdf",
                size_bytes=100,
                safe_filename="current.pdf",
                created_at=NOW,
                deleted_at=None,
            )
        )
    assert await repository.mark_candidate_promoted(completed.id, document_id, NOW)
    assert not await repository.mark_candidate_promoted(completed.id, document_id, NOW)
    records = await repository.list_candidate_analyses(WORKSPACE_A, session.id)
    assert records[0].status is CandidateAnalysisStatus.PROMOTED
    assert records[0].promoted_document_id == document_id


async def test_owned_unexpired_candidate_promotes_to_completed_collection_atomically(
    database_engine: AsyncEngine,
) -> None:
    artifacts = PostgresArtifactRepository(database_engine)
    repository = PostgresAnalysisRepository(database_engine, artifacts)
    session, _ = await create_session(repository, candidate(1))
    claimed = await repository.claim_candidate_analysis(
        "worker-a", NOW, NOW + timedelta(seconds=60)
    )
    assert claimed is not None
    assert await repository.transition_candidate_stage(
        claimed.id,
        "worker-a",
        CandidateAnalysisStatus.DOWNLOADING,
        CandidateAnalysisStatus.VALIDATING,
        NOW,
    )
    assert await repository.transition_candidate_stage(
        claimed.id,
        "worker-a",
        CandidateAnalysisStatus.VALIDATING,
        CandidateAnalysisStatus.PARSING,
        NOW,
    )
    source = await begin_artifact(
        artifacts,
        workspace_id=WORKSPACE_A,
        resource_kind=ArtifactResourceKind.ANALYSIS,
        resource_id=claimed.id,
        kind=ArtifactKind.SOURCE_PDF,
    )
    completed = await repository.complete_candidate_analysis(
        claimed.id,
        "worker-a",
        status=CandidateAnalysisStatus.READY,
        sha256="e" * 64,
        media_type="application/pdf",
        safe_filename="report.pdf",
        artifacts=((source.id, ArtifactKind.SOURCE_PDF),),
        page_count=1,
        analyzed_page_count=1,
        table_count_lower_bound=False,
        preview_page_num=None,
        preview_width=None,
        preview_height=None,
        tables=(),
        now=NOW,
    )
    document_id = uuid4()
    document_object = await begin_artifact(
        artifacts,
        workspace_id=WORKSPACE_A,
        resource_kind=ArtifactResourceKind.DOCUMENT,
        resource_id=document_id,
        kind=ArtifactKind.STORED_DOCUMENT,
        size_bytes=2048,
        sha256="e" * 64,
    )

    job = await repository.promote_candidate_to_collection(
        WORKSPACE_A,
        completed.id,
        document_id=document_id,
        artifact_object_id=document_object.id,
        size_bytes=2048,
        now=NOW + timedelta(seconds=1),
    )

    assert job is not None
    assert job.status.value == "completed"
    assert job.document_id is not None
    assert job.source_url == completed.source_url
    stored_document = await artifacts.resolve_document_object(WORKSPACE_A, document_id)
    assert stored_document is not None
    assert stored_document.state is ArtifactObjectState.AVAILABLE
    assert not await repository.promote_candidate_to_collection(
        WORKSPACE_A,
        completed.id,
        document_id=document_id,
        artifact_object_id=document_object.id,
        size_bytes=2048,
        now=NOW + timedelta(seconds=2),
    )
    assert not await repository.promote_candidate_to_collection(
        WORKSPACE_B,
        completed.id,
        document_id=document_id,
        artifact_object_id=document_object.id,
        size_bytes=2048,
        now=NOW + timedelta(seconds=2),
    )
    await create_session(repository, candidate(2))
    duplicate_candidate = await repository.claim_candidate_analysis(
        "worker-b",
        NOW + timedelta(seconds=2),
        NOW + timedelta(seconds=62),
    )
    assert duplicate_candidate is not None
    assert await repository.transition_candidate_stage(
        duplicate_candidate.id,
        "worker-b",
        CandidateAnalysisStatus.DOWNLOADING,
        CandidateAnalysisStatus.VALIDATING,
        NOW + timedelta(seconds=2),
    )
    assert await repository.transition_candidate_stage(
        duplicate_candidate.id,
        "worker-b",
        CandidateAnalysisStatus.VALIDATING,
        CandidateAnalysisStatus.PARSING,
        NOW + timedelta(seconds=2),
    )
    duplicate_source = await begin_artifact(
        artifacts,
        workspace_id=WORKSPACE_A,
        resource_kind=ArtifactResourceKind.ANALYSIS,
        resource_id=duplicate_candidate.id,
        kind=ArtifactKind.SOURCE_PDF,
        sha256="e" * 64,
    )
    duplicate_ready = await repository.complete_candidate_analysis(
        duplicate_candidate.id,
        "worker-b",
        status=CandidateAnalysisStatus.READY,
        sha256="e" * 64,
        media_type="application/pdf",
        safe_filename="duplicate.pdf",
        artifacts=((duplicate_source.id, ArtifactKind.SOURCE_PDF),),
        page_count=1,
        analyzed_page_count=1,
        table_count_lower_bound=False,
        preview_page_num=None,
        preview_width=None,
        preview_height=None,
        tables=(),
        now=NOW + timedelta(seconds=2),
    )
    duplicate_document_id = uuid4()
    duplicate_object = await begin_artifact(
        artifacts,
        workspace_id=WORKSPACE_A,
        resource_kind=ArtifactResourceKind.DOCUMENT,
        resource_id=duplicate_document_id,
        kind=ArtifactKind.STORED_DOCUMENT,
        size_bytes=2048,
        sha256="e" * 64,
    )
    duplicate_job = await repository.promote_candidate_to_collection(
        WORKSPACE_A,
        duplicate_ready.id,
        document_id=duplicate_document_id,
        artifact_object_id=duplicate_object.id,
        size_bytes=2048,
        now=NOW + timedelta(seconds=3),
    )
    assert duplicate_job is not None
    assert duplicate_job.status.value == "duplicate"
    assert duplicate_job.document_id == document_id
    async with database_engine.connect() as connection:
        discarded_state = (
            await connection.execute(
                select(artifact_objects.c.state).where(artifact_objects.c.id == duplicate_object.id)
            )
        ).scalar_one()
        duplicate_references = (
            await connection.execute(
                select(func.count())
                .select_from(artifact_references)
                .where(artifact_references.c.artifact_object_id == duplicate_object.id)
            )
        ).scalar_one()
    assert discarded_state == ArtifactObjectState.DELETING.value
    assert duplicate_references == 0
    refreshed = await repository.get_workspace_candidate_analysis(
        WORKSPACE_A,
        completed.id,
    )
    assert refreshed is not None
    assert refreshed.status is CandidateAnalysisStatus.PROMOTED
    assert refreshed.promoted_document_id == job.document_id


async def test_expired_cleanup_claim_recovers_and_deletes_candidate_artifacts_metadata(
    database_engine: AsyncEngine,
) -> None:
    artifacts = PostgresArtifactRepository(database_engine)
    repository = PostgresAnalysisRepository(database_engine, artifacts)
    session, _ = await create_session(repository, candidate(1))
    analysis_candidate = await repository.claim_candidate_analysis(
        "worker-a",
        NOW,
        NOW + timedelta(seconds=60),
    )
    assert analysis_candidate is not None
    candidate_id = analysis_candidate.id
    assert await repository.transition_candidate_stage(
        candidate_id,
        "worker-a",
        CandidateAnalysisStatus.DOWNLOADING,
        CandidateAnalysisStatus.VALIDATING,
        NOW,
    )
    assert await repository.transition_candidate_stage(
        candidate_id,
        "worker-a",
        CandidateAnalysisStatus.VALIDATING,
        CandidateAnalysisStatus.PARSING,
        NOW,
    )
    source = await begin_artifact(
        artifacts,
        workspace_id=WORKSPACE_A,
        resource_kind=ArtifactResourceKind.ANALYSIS,
        resource_id=candidate_id,
        kind=ArtifactKind.SOURCE_PDF,
    )
    preview = await begin_artifact(
        artifacts,
        workspace_id=WORKSPACE_A,
        resource_kind=ArtifactResourceKind.ANALYSIS,
        resource_id=candidate_id,
        kind=ArtifactKind.PREVIEW_PNG,
    )
    await repository.complete_candidate_analysis(
        candidate_id,
        "worker-a",
        status=CandidateAnalysisStatus.READY,
        sha256="f" * 64,
        media_type="application/pdf",
        safe_filename="expired.pdf",
        artifacts=(
            (source.id, ArtifactKind.SOURCE_PDF),
            (preview.id, ArtifactKind.PREVIEW_PNG),
        ),
        page_count=1,
        analyzed_page_count=1,
        table_count_lower_bound=False,
        preview_page_num=1,
        preview_width=1275,
        preview_height=1650,
        tables=(
            CandidateTableInput(
                page_num=1,
                table_index=0,
                bounding_box=TableBoundingBox(x=1, y=2, width=30, height=40),
                cells=(("expired",),),
                markdown="| expired |",
            ),
        ),
        now=NOW,
    )
    async with database_engine.begin() as connection:
        await connection.execute(
            update(candidate_analyses)
            .where(candidate_analyses.c.id == candidate_id)
            .values(
                status=CandidateAnalysisStatus.PROMOTED.value,
                expires_at=NOW,
                completed_at=NOW,
            )
        )

    claimed = await repository.claim_expired_candidate_cleanup(
        "cleanup-a",
        NOW,
        NOW + timedelta(seconds=60),
    )
    unavailable = await repository.claim_expired_candidate_cleanup(
        "cleanup-b",
        NOW + timedelta(seconds=30),
        NOW + timedelta(seconds=90),
    )
    recovered = await repository.claim_expired_candidate_cleanup(
        "cleanup-b",
        NOW + timedelta(seconds=61),
        NOW + timedelta(seconds=121),
    )

    assert claimed is not None
    assert claimed.id == candidate_id
    assert claimed.status is CandidateAnalysisStatus.PROMOTED
    assert unavailable is None
    assert recovered is not None
    assert recovered.claimed_by == "cleanup-b"
    assert not await repository.delete_expired_candidate_cleanup(
        candidate_id,
        "cleanup-a",
        NOW + timedelta(seconds=61),
    )
    assert await repository.delete_expired_candidate_cleanup(
        candidate_id,
        "cleanup-b",
        NOW + timedelta(seconds=61),
    )
    assert not await repository.delete_expired_candidate_cleanup(
        candidate_id,
        "cleanup-b",
        NOW + timedelta(seconds=61),
    )
    assert await repository.get_analysis_session(WORKSPACE_A, session.id) is None
    async with database_engine.connect() as connection:
        remaining_tables = (
            await connection.execute(
                select(func.count())
                .select_from(candidate_tables)
                .where(candidate_tables.c.candidate_analysis_id == candidate_id)
            )
        ).scalar_one()
        remaining_references = (
            await connection.execute(
                select(func.count())
                .select_from(artifact_references)
                .where(artifact_references.c.candidate_analysis_id == candidate_id)
            )
        ).scalar_one()
        orphaned_objects = (
            await connection.execute(
                select(func.count())
                .select_from(artifact_objects)
                .where(
                    artifact_objects.c.id.in_((source.id, preview.id)),
                    artifact_objects.c.state == ArtifactObjectState.AVAILABLE.value,
                )
            )
        ).scalar_one()
    assert remaining_tables == 0
    assert remaining_references == 0
    assert orphaned_objects == 2
