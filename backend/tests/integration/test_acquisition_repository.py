import os
from datetime import UTC, datetime, timedelta
from importlib.util import find_spec
from uuid import UUID, uuid4

import pytest
from anyio import Path
from sqlalchemy import URL, insert
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from parserium_collector.adapters.database.tables import documents, metadata, workspaces
from parserium_collector.features.acquisition.models import (
    CollectionCandidate,
    CollectionJobStatus,
    DocumentType,
    ExportStatus,
)
from parserium_collector.features.storage.keys import artifact_key
from parserium_collector.features.storage.models import (
    ArtifactKind,
    ArtifactResourceKind,
    StoredObjectMetadata,
)
from parserium_collector.features.storage.repository import PostgresArtifactRepository

WORKSPACE_A = UUID("10000000-0000-4000-8000-000000000001")
WORKSPACE_B = UUID("10000000-0000-4000-8000-000000000002")
MIGRATION_TIME = datetime(2026, 8, 25, 11, 0, tzinfo=UTC)


def repository_type():
    try:
        specification = find_spec("parserium_collector.features.acquisition.repository")
    except ModuleNotFoundError:
        specification = None
    if specification is None:
        pytest.fail("acquisition repository is not implemented")

    from parserium_collector.features.acquisition.repository import (
        PostgresAcquisitionRepository,
    )

    return PostgresAcquisitionRepository


async def reset_database(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(metadata.drop_all)
        await connection.run_sync(metadata.create_all)
        await connection.execute(
            insert(workspaces),
            (
                {
                    "id": WORKSPACE_A,
                    "name": "Workspace A",
                    "is_local": False,
                    "created_at": MIGRATION_TIME,
                    "updated_at": MIGRATION_TIME,
                },
                {
                    "id": WORKSPACE_B,
                    "name": "Workspace B",
                    "is_local": False,
                    "created_at": MIGRATION_TIME,
                    "updated_at": MIGRATION_TIME,
                },
            ),
        )


def pdf_candidate(index: int) -> CollectionCandidate:
    return CollectionCandidate(
        url=f"https://documents.example/report-{index}.pdf",
        title=f"Report {index}",
        document_type=DocumentType.PDF,
    )


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
    try:
        await reset_database(engine)
        yield engine
    finally:
        await engine.dispose()


async def register_and_complete(
    database_engine: AsyncEngine,
    repository: object,
    job_id: UUID,
    worker_id: str,
    *,
    workspace_id: UUID,
    sha256: str,
    size_bytes: int,
    safe_filename: str,
    now: datetime,
):
    artifact_repository = PostgresArtifactRepository(database_engine)
    document_id = uuid4()
    storage_key = artifact_key(
        workspace_id,
        ArtifactResourceKind.DOCUMENT,
        document_id,
        ArtifactKind.STORED_DOCUMENT,
    )
    object_metadata = StoredObjectMetadata(
        storage_key=storage_key,
        media_type="application/pdf",
        size_bytes=size_bytes,
        sha256=sha256,
    )
    artifact_object = await artifact_repository.begin_upload(
        workspace_id,
        storage_key,
        object_metadata,
        now=now,
    )
    return await repository.complete_collection(
        job_id,
        worker_id,
        sha256=sha256,
        document_type=DocumentType.PDF,
        media_type="application/pdf",
        size_bytes=size_bytes,
        document_id=document_id,
        artifact_object_id=artifact_object.id,
        safe_filename=safe_filename,
        now=now,
    )


async def test_claim_is_exclusive_and_an_expired_lease_is_recovered(
    database_engine: AsyncEngine,
) -> None:
    repository = repository_type()(database_engine)
    now = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
    created = await repository.create_collection_jobs(WORKSPACE_A, (pdf_candidate(1),), now)

    claimed = await repository.claim_collection_job(
        "worker-a",
        now,
        now + timedelta(seconds=60),
    )
    unavailable = await repository.claim_collection_job(
        "worker-b",
        now + timedelta(seconds=30),
        now + timedelta(seconds=90),
    )
    recovered = await repository.claim_collection_job(
        "worker-b",
        now + timedelta(seconds=61),
        now + timedelta(seconds=121),
    )

    assert claimed is not None
    assert claimed.id == created[0].id
    assert claimed.status is CollectionJobStatus.DOWNLOADING
    assert claimed.claimed_by == "worker-a"
    assert claimed.attempt_count == 1
    assert unavailable is None
    assert recovered is not None
    assert recovered.id == claimed.id
    assert recovered.claimed_by == "worker-b"
    assert recovered.attempt_count == 2


async def test_live_collection_lease_can_only_be_renewed_by_its_owner(
    database_engine: AsyncEngine,
) -> None:
    repository = repository_type()(database_engine)
    now = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
    await repository.create_collection_jobs(WORKSPACE_A, (pdf_candidate(1),), now)
    claimed = await repository.claim_collection_job(
        "worker-a",
        now,
        now + timedelta(seconds=60),
    )
    assert claimed is not None

    assert (
        await repository.renew_collection_lease(
            claimed.id,
            "worker-b",
            now + timedelta(seconds=30),
            now + timedelta(seconds=120),
        )
        is False
    )
    assert (
        await repository.renew_collection_lease(
            claimed.id,
            "worker-a",
            now + timedelta(seconds=30),
            now + timedelta(seconds=120),
        )
        is True
    )
    unavailable = await repository.claim_collection_job(
        "worker-b",
        now + timedelta(seconds=61),
        now + timedelta(seconds=121),
    )
    recovered = await repository.claim_collection_job(
        "worker-b",
        now + timedelta(seconds=121),
        now + timedelta(seconds=181),
    )

    assert unavailable is None
    assert recovered is not None
    assert recovered.id == claimed.id


async def test_scheduled_retry_is_unavailable_until_retry_time(
    database_engine: AsyncEngine,
) -> None:
    repository = repository_type()(database_engine)
    now = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
    await repository.create_collection_jobs(WORKSPACE_A, (pdf_candidate(1),), now)
    claimed = await repository.claim_collection_job(
        "worker-a",
        now,
        now + timedelta(seconds=60),
    )
    assert claimed is not None
    retry_at = now + timedelta(seconds=30)
    await repository.fail_collection(
        claimed.id,
        "worker-a",
        error_code="download_timeout",
        error_detail="The document server timed out.",
        retryable=True,
        retry_at=retry_at,
        now=now,
    )

    unavailable = await repository.claim_collection_job(
        "worker-b",
        retry_at - timedelta(seconds=1),
        retry_at + timedelta(seconds=59),
    )
    retried = await repository.claim_collection_job(
        "worker-b",
        retry_at,
        retry_at + timedelta(seconds=60),
    )

    assert unavailable is None
    assert retried is not None
    assert retried.id == claimed.id
    assert retried.attempt_count == 2
    assert retried.error_code is None


async def test_progress_completion_and_content_deduplication(
    database_engine: AsyncEngine,
) -> None:
    repository = repository_type()(database_engine)
    now = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
    created = await repository.create_collection_jobs(
        WORKSPACE_A,
        (pdf_candidate(1), pdf_candidate(2)),
        now,
    )

    first = await repository.claim_collection_job("worker-a", now, now + timedelta(minutes=1))
    assert first is not None
    await repository.update_collection_progress(first.id, "worker-a", 512, 1024, now)
    await repository.mark_collection_validating(first.id, "worker-a", now)
    completed = await register_and_complete(
        database_engine,
        repository,
        first.id,
        "worker-a",
        workspace_id=WORKSPACE_A,
        sha256="a" * 64,
        size_bytes=1024,
        safe_filename="report.pdf",
        now=now,
    )

    second = await repository.claim_collection_job("worker-a", now, now + timedelta(minutes=1))
    assert second is not None
    await repository.mark_collection_validating(second.id, "worker-a", now)
    duplicate = await register_and_complete(
        database_engine,
        repository,
        second.id,
        "worker-a",
        workspace_id=WORKSPACE_A,
        sha256="a" * 64,
        size_bytes=1024,
        safe_filename="another-name.pdf",
        now=now,
    )
    artifact_repository = PostgresArtifactRepository(database_engine)
    usage = await artifact_repository.get_usage(WORKSPACE_A)
    deletion_claims = await artifact_repository.claim_deletions(
        "maintenance-worker",
        now=now,
        lease_expires_at=now + timedelta(minutes=1),
        limit=10,
    )
    documents = await repository.list_documents(WORKSPACE_A, limit=10)
    jobs = await repository.list_collection_jobs(WORKSPACE_A, limit=10)

    assert completed.status is CollectionJobStatus.COMPLETED
    assert completed.bytes_downloaded == 512
    assert completed.content_length == 1024
    assert completed.document_id is not None
    assert duplicate.status is CollectionJobStatus.DUPLICATE
    assert duplicate.document_id == completed.document_id
    assert (usage.retained_bytes, usage.retained_objects) == (1024, 1)
    assert len(deletion_claims) == 1
    assert deletion_claims[0].available_at is None
    assert len(documents) == 1
    assert {job.id for job in jobs} == {job.id for job in created}
    assert await repository.get_collection_job(WORKSPACE_A, completed.id) == completed
    assert await repository.get_collection_job(WORKSPACE_A, uuid4()) is None
    assert await repository.get_document(WORKSPACE_A, documents[0].id) == documents[0]
    assert await repository.get_document(WORKSPACE_A, uuid4()) is None
    stored_object = await artifact_repository.resolve_document_object(
        WORKSPACE_A,
        documents[0].id,
    )
    assert stored_object is not None
    assert stored_object.storage_key.startswith(f"workspaces/{WORKSPACE_A}/document/")
    assert await artifact_repository.resolve_document_object(WORKSPACE_B, documents[0].id) is None


async def test_collection_activity_and_documents_use_stable_cursor_pages(
    database_engine: AsyncEngine,
) -> None:
    repository = repository_type()(database_engine)
    started_at = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
    created_job_ids = []
    created_document_ids = []

    for index, digest_character in enumerate(("a", "b", "c"), start=1):
        now = started_at + timedelta(minutes=index)
        created = await repository.create_collection_jobs(WORKSPACE_A, (pdf_candidate(index),), now)
        created_job_ids.append(created[0].id)
        claimed = await repository.claim_collection_job(
            "worker-a",
            now,
            now + timedelta(minutes=1),
        )
        assert claimed is not None
        await repository.mark_collection_validating(claimed.id, "worker-a", now)
        completed = await register_and_complete(
            database_engine,
            repository,
            claimed.id,
            "worker-a",
            workspace_id=WORKSPACE_A,
            sha256=digest_character * 64,
            size_bytes=index * 100,
            safe_filename=f"report-{index}.pdf",
            now=now,
        )
        assert completed.document_id is not None
        created_document_ids.append(completed.document_id)

    first_jobs = await repository.list_collection_jobs_page(WORKSPACE_A, limit=2, cursor=None)
    assert first_jobs.next_cursor is not None
    second_jobs = await repository.list_collection_jobs_page(
        WORKSPACE_A,
        limit=2,
        cursor=first_jobs.next_cursor,
    )

    first_documents = await repository.list_documents_page(WORKSPACE_A, limit=2, cursor=None)
    assert first_documents.next_cursor is not None
    second_documents = await repository.list_documents_page(
        WORKSPACE_A,
        limit=2,
        cursor=first_documents.next_cursor,
    )

    assert first_jobs.total == second_jobs.total == 3
    assert [job.id for job in (*first_jobs.items, *second_jobs.items)] == list(
        reversed(created_job_ids)
    )
    assert second_jobs.next_cursor is None
    assert first_documents.total == second_documents.total == 3
    assert [document.id for document in (*first_documents.items, *second_documents.items)] == list(
        reversed(created_document_ids)
    )
    assert second_documents.next_cursor is None


async def test_manual_retry_only_requeues_retryable_failed_jobs(
    database_engine: AsyncEngine,
) -> None:
    repository = repository_type()(database_engine)
    now = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
    await repository.create_collection_jobs(WORKSPACE_A, (pdf_candidate(1),), now)

    retryable = await repository.claim_collection_job("worker-a", now, now + timedelta(minutes=1))
    assert retryable is not None
    await repository.fail_collection(
        retryable.id,
        "worker-a",
        error_code="download_timeout",
        error_detail="The document server timed out.",
        retryable=True,
        retry_at=None,
        now=now,
    )
    assert (
        await repository.retry_collection_job(WORKSPACE_A, retryable.id, now + timedelta(minutes=1))
        is True
    )

    reclaimed = await repository.claim_collection_job(
        "worker-b",
        now + timedelta(minutes=1),
        now + timedelta(minutes=2),
    )
    assert reclaimed is not None
    assert reclaimed.id == retryable.id
    assert reclaimed.error_code is None

    await repository.fail_collection(
        reclaimed.id,
        "worker-b",
        error_code="blocked_destination",
        error_detail="The destination is not allowed.",
        retryable=False,
        retry_at=None,
        now=now + timedelta(minutes=1),
    )
    assert (
        await repository.retry_collection_job(WORKSPACE_A, reclaimed.id, now + timedelta(minutes=2))
        is False
    )


async def test_clear_completed_history_preserves_jobs_documents_and_exports_in_use(
    database_engine: AsyncEngine,
) -> None:
    repository = repository_type()(database_engine)
    now = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
    await repository.create_collection_jobs(
        WORKSPACE_A,
        tuple(pdf_candidate(index) for index in range(1, 5)),
        now,
    )

    completed = await repository.claim_collection_job("worker-a", now, now + timedelta(minutes=1))
    assert completed is not None
    await repository.mark_collection_validating(completed.id, "worker-a", now)
    completed = await register_and_complete(
        database_engine,
        repository,
        completed.id,
        "worker-a",
        workspace_id=WORKSPACE_A,
        sha256="d" * 64,
        size_bytes=256,
        safe_filename="retained.pdf",
        now=now,
    )
    assert completed.document_id is not None

    duplicate = await repository.claim_collection_job("worker-a", now, now + timedelta(minutes=1))
    assert duplicate is not None
    await repository.mark_collection_validating(duplicate.id, "worker-a", now)
    duplicate = await register_and_complete(
        database_engine,
        repository,
        duplicate.id,
        "worker-a",
        workspace_id=WORKSPACE_A,
        sha256="d" * 64,
        size_bytes=256,
        safe_filename="duplicate.pdf",
        now=now,
    )

    failed = await repository.claim_collection_job("worker-a", now, now + timedelta(minutes=1))
    assert failed is not None
    await repository.fail_collection(
        failed.id,
        "worker-a",
        error_code="blocked_destination",
        error_detail="The destination is not allowed.",
        retryable=False,
        retry_at=None,
        now=now,
    )
    export = await repository.create_export(
        WORKSPACE_A,
        completed.document_id,
        "bank/2026",
        "retained.pdf",
        now,
    )

    deleted_count = await repository.delete_completed_collection_jobs(WORKSPACE_A)

    remaining_jobs = await repository.list_collection_jobs(WORKSPACE_A, limit=10)
    documents = await repository.list_documents(WORKSPACE_A, limit=10)
    exports = await repository.list_exports(WORKSPACE_A, limit=10)
    assert deleted_count == 2
    assert {job.status for job in remaining_jobs} == {
        CollectionJobStatus.QUEUED,
        CollectionJobStatus.FAILED,
    }
    assert len(documents) == 1
    assert documents[0].id == completed.document_id == duplicate.document_id
    assert exports == (export,)


async def test_export_jobs_are_claimed_and_completed(
    database_engine: AsyncEngine,
) -> None:
    repository = repository_type()(database_engine)
    now = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
    await repository.create_collection_jobs(WORKSPACE_A, (pdf_candidate(1),), now)
    job = await repository.claim_collection_job("worker-a", now, now + timedelta(minutes=1))
    assert job is not None
    await repository.mark_collection_validating(job.id, "worker-a", now)
    completed = await register_and_complete(
        database_engine,
        repository,
        job.id,
        "worker-a",
        workspace_id=WORKSPACE_A,
        sha256="b" * 64,
        size_bytes=128,
        safe_filename="report.pdf",
        now=now,
    )
    assert completed.document_id is not None
    export = await repository.create_export(
        WORKSPACE_A,
        completed.document_id,
        "bank/2026",
        "report.pdf",
        now,
    )

    claimed = await repository.claim_export("worker-a", now, now + timedelta(minutes=1))
    unavailable = await repository.claim_export("worker-b", now, now + timedelta(minutes=1))
    assert claimed is not None
    assert claimed.id == export.id
    assert claimed.status is ExportStatus.EXPORTING
    assert unavailable is None
    assert (
        await repository.renew_export_lease(
            claimed.id,
            "worker-b",
            now + timedelta(seconds=30),
            now + timedelta(minutes=2),
        )
        is False
    )
    assert (
        await repository.renew_export_lease(
            claimed.id,
            "worker-a",
            now + timedelta(seconds=30),
            now + timedelta(minutes=2),
        )
        is True
    )

    finished = await repository.complete_export(
        claimed.id,
        "worker-a",
        "bank/2026/report.pdf",
        now,
    )
    exports = await repository.list_exports(WORKSPACE_A, limit=10)

    assert finished.status is ExportStatus.COMPLETED
    assert finished.exported_relative_path == "bank/2026/report.pdf"
    assert exports == (finished,)


async def test_export_failure_can_be_scheduled_or_terminal(
    database_engine: AsyncEngine,
) -> None:
    repository = repository_type()(database_engine)
    now = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
    await repository.create_collection_jobs(WORKSPACE_A, (pdf_candidate(1),), now)
    job = await repository.claim_collection_job("worker-a", now, now + timedelta(minutes=1))
    assert job is not None
    await repository.mark_collection_validating(job.id, "worker-a", now)
    completed = await register_and_complete(
        database_engine,
        repository,
        job.id,
        "worker-a",
        workspace_id=WORKSPACE_A,
        sha256="c" * 64,
        size_bytes=128,
        safe_filename="report.pdf",
        now=now,
    )
    assert completed.document_id is not None
    export = await repository.create_export(
        WORKSPACE_A,
        completed.document_id,
        "bank/2026",
        "report.pdf",
        now,
    )
    claimed = await repository.claim_export("worker-a", now, now + timedelta(minutes=1))
    assert claimed is not None
    retry_at = now + timedelta(seconds=30)
    await repository.fail_export(
        export.id,
        "worker-a",
        error_code="export_unavailable",
        error_detail="The export destination is temporarily unavailable.",
        retry_at=retry_at,
        now=now,
    )

    assert (
        await repository.claim_export(
            "worker-b",
            retry_at - timedelta(seconds=1),
            retry_at + timedelta(seconds=59),
        )
        is None
    )
    retried = await repository.claim_export(
        "worker-b",
        retry_at,
        retry_at + timedelta(seconds=60),
    )
    assert retried is not None
    assert retried.attempt_count == 2

    await repository.fail_export(
        retried.id,
        "worker-b",
        error_code="export_invalid",
        error_detail="The configured export destination is invalid.",
        retry_at=None,
        now=retry_at,
    )
    failed = (await repository.list_exports(WORKSPACE_A, limit=10))[0]

    assert failed.status is ExportStatus.FAILED
    assert failed.error_code == "export_invalid"
    assert failed.completed_at == retry_at


async def test_workspace_isolation_and_per_workspace_content_deduplication(
    database_engine: AsyncEngine,
) -> None:
    repository = repository_type()(database_engine)
    now = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
    later = now + timedelta(minutes=1)
    await repository.create_collection_jobs(WORKSPACE_A, (pdf_candidate(1),), now)
    await repository.create_collection_jobs(WORKSPACE_B, (pdf_candidate(2),), later)

    first = await repository.claim_collection_job("worker-a", now, now + timedelta(seconds=30))
    assert first is not None
    assert first.workspace_id == WORKSPACE_A
    await repository.mark_collection_validating(first.id, "worker-a", now)
    completed_a = await register_and_complete(
        database_engine,
        repository,
        first.id,
        "worker-a",
        workspace_id=WORKSPACE_A,
        sha256="e" * 64,
        size_bytes=128,
        safe_filename="report-a.pdf",
        now=now,
    )

    second = await repository.claim_collection_job("worker-b", later, later + timedelta(seconds=30))
    assert second is not None
    assert second.workspace_id == WORKSPACE_B
    await repository.mark_collection_validating(second.id, "worker-b", later)
    completed_b = await register_and_complete(
        database_engine,
        repository,
        second.id,
        "worker-b",
        workspace_id=WORKSPACE_B,
        sha256="e" * 64,
        size_bytes=128,
        safe_filename="report-b.pdf",
        now=later,
    )

    documents_a = await repository.list_documents(WORKSPACE_A, limit=10)
    documents_b = await repository.list_documents(WORKSPACE_B, limit=10)
    assert len(documents_a) == len(documents_b) == 1
    assert completed_a.document_id != completed_b.document_id
    assert await repository.get_collection_job(WORKSPACE_B, completed_a.id) is None
    assert await repository.get_document(WORKSPACE_B, documents_a[0].id) is None
    with pytest.raises(LookupError):
        await repository.create_export(
            WORKSPACE_B,
            documents_a[0].id,
            "bank/2026",
            "report-a.pdf",
            later,
        )

    assert await repository.delete_completed_collection_jobs(WORKSPACE_A) == 1
    jobs_b = await repository.list_collection_jobs(WORKSPACE_B, limit=10)
    assert jobs_b == (completed_b,)


async def test_document_soft_delete_is_atomic_owned_and_idempotent(
    database_engine: AsyncEngine,
) -> None:
    repository = repository_type()(database_engine)
    artifacts = PostgresArtifactRepository(database_engine)
    now = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
    await repository.create_collection_jobs(WORKSPACE_A, (pdf_candidate(1),), now)
    job = await repository.claim_collection_job(
        "worker-a",
        now,
        now + timedelta(minutes=1),
    )
    assert job is not None
    await repository.mark_collection_validating(job.id, "worker-a", now)
    completed = await register_and_complete(
        database_engine,
        repository,
        job.id,
        "worker-a",
        workspace_id=WORKSPACE_A,
        sha256="f" * 64,
        size_bytes=256,
        safe_filename="delete-me.pdf",
        now=now,
    )
    assert completed.document_id is not None
    object_before_delete = await artifacts.resolve_document_object(
        WORKSPACE_A,
        completed.document_id,
    )
    assert object_before_delete is not None

    assert not await repository.delete_document(WORKSPACE_B, completed.document_id, now)
    assert await repository.delete_document(WORKSPACE_A, completed.document_id, now)
    assert await repository.delete_document(WORKSPACE_A, completed.document_id, now)

    assert await repository.get_document(WORKSPACE_A, completed.document_id) is None
    assert await repository.list_documents(WORKSPACE_A, 10) == ()
    assert await artifacts.resolve_document_object(WORKSPACE_A, completed.document_id) is None
    deletions = await artifacts.claim_deletions(
        "maintenance-worker",
        now=now,
        lease_expires_at=now + timedelta(minutes=1),
        limit=10,
    )
    assert [record.id for record in deletions] == [object_before_delete.id]


async def test_document_delete_rolls_back_when_its_reference_is_missing(
    database_engine: AsyncEngine,
) -> None:
    repository = repository_type()(database_engine)
    now = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
    document_id = uuid4()
    async with database_engine.begin() as connection:
        await connection.execute(
            insert(documents).values(
                id=document_id,
                workspace_id=WORKSPACE_A,
                sha256="9" * 64,
                document_type="pdf",
                media_type="application/pdf",
                size_bytes=64,
                safe_filename="inconsistent.pdf",
                created_at=now,
                deleted_at=None,
            )
        )

    with pytest.raises(LookupError):
        await repository.delete_document(WORKSPACE_A, document_id, now)

    assert await repository.get_document(WORKSPACE_A, document_id) is not None
