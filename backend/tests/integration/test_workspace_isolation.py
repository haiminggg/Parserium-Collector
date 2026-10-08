import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import URL
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from parserium_collector.adapters.database.tables import metadata
from parserium_collector.features.acquisition.models import DocumentType
from parserium_collector.features.acquisition.repository import (
    PostgresAcquisitionRepository,
)
from parserium_collector.features.analysis.models import (
    AnalysisSearchRequest,
    CandidateAnalysisStatus,
)
from parserium_collector.features.analysis.repository import PostgresAnalysisRepository
from parserium_collector.features.discovery.models import (
    DocumentCandidate,
    DocumentDiscoveryResponse,
)
from parserium_collector.features.discovery.scoped import ScopedDiscoveryResult
from parserium_collector.features.identity.models import WorkspaceRole, WorkspaceScope
from parserium_collector.features.identity.repository import PostgresIdentityRepository
from parserium_collector.features.storage.keys import artifact_key
from parserium_collector.features.storage.models import (
    ArtifactKind,
    ArtifactResourceKind,
    StoredObjectMetadata,
)
from parserium_collector.features.storage.repository import PostgresArtifactRepository

NOW = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)


def _database_url() -> str:
    password_file = Path(os.environ["TEST_DATABASE_PASSWORD_FILE"])
    return URL.create(
        "postgresql+psycopg",
        username="parserium_collector",
        password=password_file.read_text(encoding="utf-8").strip(),
        host=os.environ["TEST_DATABASE_HOST"],
        port=5432,
        database="parserium_collector",
    ).render_as_string(hide_password=False)


@pytest.fixture
async def database_engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(_database_url())
    async with engine.begin() as connection:
        await connection.run_sync(metadata.drop_all)
        await connection.run_sync(metadata.create_all)
    try:
        yield engine
    finally:
        async with engine.begin() as connection:
            await connection.run_sync(metadata.drop_all)
        await engine.dispose()


def _candidate(name: str) -> DocumentCandidate:
    return DocumentCandidate(
        url=f"https://documents.example/{name}.pdf",
        title=f"{name.title()} report",
        description=f"Workspace-owned {name} report",
        document_type="pdf",
    )


async def _create_hosted_scope(
    repository: PostgresIdentityRepository,
    *,
    email: str,
    workspace_name: str,
    invitation_digest: str,
    session_digest: str,
    subject: str,
) -> WorkspaceScope:
    await repository.create_workspace_invitation(
        workspace_name=workspace_name,
        workspace_id=None,
        normalized_email=email,
        token_digest=invitation_digest,
        role=WorkspaceRole.OWNER,
        created_at=NOW,
        expires_at=NOW + timedelta(hours=1),
    )
    login = await repository.redeem_invitation_and_create_session(
        token_digest=invitation_digest,
        issuer="https://identity.parserium.test",
        subject=subject,
        email=email,
        normalized_email=email,
        display_name=f"{workspace_name} Owner",
        session_digest=session_digest,
        now=NOW + timedelta(minutes=1),
    )
    assert await repository.load_hosted_session(session_digest) is not None
    return WorkspaceScope(
        workspace_id=login.workspace_id,
        user_id=login.user_id,
        role=login.role,
    )


@pytest.mark.asyncio
async def test_two_hosted_workspaces_cannot_read_or_mutate_each_others_resources(
    database_engine: AsyncEngine,
) -> None:
    identity = PostgresIdentityRepository(database_engine)
    artifacts = PostgresArtifactRepository(database_engine)
    analysis = PostgresAnalysisRepository(database_engine, artifacts)
    acquisition = PostgresAcquisitionRepository(database_engine, artifacts)
    scope_a = await _create_hosted_scope(
        identity,
        email="owner-a@parserium.test",
        workspace_name="Workspace A",
        invitation_digest="a" * 64,
        session_digest="b" * 64,
        subject="user-a",
    )
    scope_b = await _create_hosted_scope(
        identity,
        email="owner-b@parserium.test",
        workspace_name="Workspace B",
        invitation_digest="c" * 64,
        session_digest="d" * 64,
        subject="user-b",
    )

    analysis_a, candidates_a = await analysis.create_analysis_session(
        scope_a,
        AnalysisSearchRequest(query="workspace a tables", limit=1),
        ScopedDiscoveryResult(
            response=DocumentDiscoveryResponse(
                provider_search_ids=["search-a"],
                candidates=[_candidate("analysis-a")],
                rejected_non_document_results=0,
            ),
            connection_id=None,
            connection_name_snapshot=None,
            connection_type_snapshot=None,
        ),
        NOW + timedelta(minutes=2),
        NOW + timedelta(hours=1),
        10_000_000,
    )
    analysis_b, candidates_b = await analysis.create_analysis_session(
        scope_b,
        AnalysisSearchRequest(query="workspace b tables", limit=1),
        ScopedDiscoveryResult(
            response=DocumentDiscoveryResponse(
                provider_search_ids=["search-b"],
                candidates=[_candidate("analysis-b")],
                rejected_non_document_results=0,
            ),
            connection_id=None,
            connection_name_snapshot=None,
            connection_type_snapshot=None,
        ),
        NOW + timedelta(minutes=3),
        NOW + timedelta(hours=1),
        10_000_000,
    )

    claimed_candidate = await analysis.claim_candidate_analysis(
        "analysis-worker",
        NOW + timedelta(minutes=4),
        NOW + timedelta(minutes=5),
    )
    assert claimed_candidate is not None
    assert claimed_candidate.id == candidates_a[0].id
    assert await analysis.transition_candidate_stage(
        claimed_candidate.id,
        "analysis-worker",
        CandidateAnalysisStatus.DOWNLOADING,
        CandidateAnalysisStatus.VALIDATING,
        NOW + timedelta(minutes=4),
    )
    assert await analysis.transition_candidate_stage(
        claimed_candidate.id,
        "analysis-worker",
        CandidateAnalysisStatus.VALIDATING,
        CandidateAnalysisStatus.PARSING,
        NOW + timedelta(minutes=4),
    )
    analysis_storage_key = artifact_key(
        scope_a.workspace_id,
        ArtifactResourceKind.ANALYSIS,
        claimed_candidate.id,
        ArtifactKind.SOURCE_PDF,
    )
    analysis_object = await artifacts.begin_upload(
        scope_a.workspace_id,
        analysis_storage_key,
        StoredObjectMetadata(
            storage_key=analysis_storage_key,
            media_type="application/pdf",
            size_bytes=128,
            sha256="e" * 64,
        ),
        now=NOW + timedelta(minutes=4),
    )
    ready_candidate = await analysis.complete_candidate_analysis(
        claimed_candidate.id,
        "analysis-worker",
        status=CandidateAnalysisStatus.READY,
        sha256="e" * 64,
        media_type="application/pdf",
        safe_filename="analysis-a.pdf",
        artifacts=((analysis_object.id, ArtifactKind.SOURCE_PDF),),
        page_count=1,
        analyzed_page_count=1,
        table_count_lower_bound=False,
        preview_page_num=None,
        preview_width=None,
        preview_height=None,
        tables=(),
        now=NOW + timedelta(minutes=4),
    )

    jobs_a = await acquisition.create_collection_jobs(
        scope_a.workspace_id,
        (_candidate("retry-a"), _candidate("document-a")),
        NOW + timedelta(minutes=5),
    )
    failed_a = await acquisition.claim_collection_job(
        "acquisition-worker",
        NOW + timedelta(minutes=5),
        NOW + timedelta(minutes=6),
    )
    assert failed_a is not None
    assert failed_a.id == jobs_a[0].id
    await acquisition.fail_collection(
        failed_a.id,
        "acquisition-worker",
        error_code="download_timeout",
        error_detail="The document server timed out.",
        retryable=True,
        retry_at=None,
        now=NOW + timedelta(minutes=5),
    )

    document_job_a = await acquisition.claim_collection_job(
        "acquisition-worker",
        NOW + timedelta(minutes=6),
        NOW + timedelta(minutes=7),
    )
    assert document_job_a is not None
    assert document_job_a.id == jobs_a[1].id
    await acquisition.mark_collection_validating(
        document_job_a.id,
        "acquisition-worker",
        NOW + timedelta(minutes=6),
    )
    document_id = uuid4()
    document_storage_key = artifact_key(
        scope_a.workspace_id,
        ArtifactResourceKind.DOCUMENT,
        document_id,
        ArtifactKind.STORED_DOCUMENT,
    )
    document_object = await artifacts.begin_upload(
        scope_a.workspace_id,
        document_storage_key,
        StoredObjectMetadata(
            storage_key=document_storage_key,
            media_type="application/pdf",
            size_bytes=256,
            sha256="f" * 64,
        ),
        now=NOW + timedelta(minutes=6),
    )
    completed_a = await acquisition.complete_collection(
        document_job_a.id,
        "acquisition-worker",
        sha256="f" * 64,
        document_type=DocumentType.PDF,
        media_type="application/pdf",
        size_bytes=256,
        document_id=document_id,
        artifact_object_id=document_object.id,
        safe_filename="document-a.pdf",
        now=NOW + timedelta(minutes=6),
    )
    assert completed_a.document_id is not None
    export_a = await acquisition.create_export(
        scope_a.workspace_id,
        completed_a.document_id,
        "workspace-a",
        "document-a.pdf",
        NOW + timedelta(minutes=7),
    )

    jobs_b = await acquisition.create_collection_jobs(
        scope_b.workspace_id,
        (_candidate("document-b"),),
        NOW + timedelta(minutes=8),
    )

    assert await analysis.get_analysis_session(scope_b.workspace_id, analysis_a.id) is None
    assert await analysis.get_analysis_session(scope_a.workspace_id, analysis_b.id) is None
    assert await analysis.list_candidate_analyses(scope_b.workspace_id, analysis_a.id) == ()
    assert (
        await analysis.get_workspace_candidate_analysis(scope_b.workspace_id, ready_candidate.id)
        is None
    )
    assert await analysis.list_candidate_tables(scope_b.workspace_id, ready_candidate.id) == ()
    assert not await analysis.cancel_analysis_session(
        scope_b.workspace_id,
        analysis_a.id,
        NOW + timedelta(minutes=9),
    )
    assert not await analysis.promote_candidate_to_collection(
        scope_b.workspace_id,
        ready_candidate.id,
        document_id=uuid4(),
        artifact_object_id=uuid4(),
        size_bytes=256,
        now=NOW + timedelta(minutes=9),
    )

    assert await acquisition.get_collection_job(scope_b.workspace_id, failed_a.id) is None
    assert await acquisition.get_document(scope_b.workspace_id, completed_a.document_id) is None
    assert not await acquisition.retry_collection_job(
        scope_b.workspace_id,
        failed_a.id,
        NOW + timedelta(minutes=9),
    )
    with pytest.raises(LookupError):
        await acquisition.create_export(
            scope_b.workspace_id,
            completed_a.document_id,
            "workspace-b",
            "document-a.pdf",
            NOW + timedelta(minutes=9),
        )
    assert not await acquisition.delete_document(
        scope_b.workspace_id,
        completed_a.document_id,
        NOW + timedelta(minutes=9),
    )
    assert await acquisition.delete_document(
        scope_a.workspace_id,
        completed_a.document_id,
        NOW + timedelta(minutes=9),
    )
    assert await acquisition.delete_document(
        scope_a.workspace_id,
        completed_a.document_id,
        NOW + timedelta(minutes=9),
    )
    assert await acquisition.get_document(scope_a.workspace_id, completed_a.document_id) is None

    assert {
        job.id for job in await acquisition.list_collection_jobs(scope_a.workspace_id, limit=20)
    } == {job.id for job in jobs_a}
    assert {
        job.id for job in await acquisition.list_collection_jobs(scope_b.workspace_id, limit=20)
    } == {jobs_b[0].id}
    assert await acquisition.list_documents(scope_a.workspace_id, limit=20) == ()
    assert await acquisition.list_documents(scope_b.workspace_id, limit=20) == ()
    assert await acquisition.list_exports(scope_a.workspace_id, limit=20) == (export_a,)
    assert await acquisition.list_exports(scope_b.workspace_id, limit=20) == ()
    assert candidates_b[0].workspace_id == scope_b.workspace_id
