import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import anyio
import anyio.lowlevel
import pytest
from anyio import Path
from sqlalchemy import URL, insert, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from parserium_collector.adapters.database.tables import (
    artifact_objects,
    artifact_references,
    candidate_analyses,
    discovery_analysis_sessions,
    documents,
    local_sessions,
    metadata,
    workspace_storage_usage,
    workspaces,
)
from parserium_collector.features.storage.models import (
    ArtifactKind,
    ArtifactLifecycle,
    ArtifactObjectState,
    StoredObjectMetadata,
)
from parserium_collector.features.storage.repository import PostgresArtifactRepository

WORKSPACE_ID = UUID("70000000-0000-4000-8000-000000000001")
DOCUMENT_A = UUID("71000000-0000-4000-8000-000000000001")
DOCUMENT_B = UUID("71000000-0000-4000-8000-000000000002")
SESSION_ID = UUID("72000000-0000-4000-8000-000000000001")
CANDIDATE_ID = UUID("73000000-0000-4000-8000-000000000001")
OWNER_DIGEST = "e" * 64
NOW = datetime(2026, 8, 31, 14, 0, tzinfo=UTC)
KEY = f"workspaces/{WORKSPACE_ID}/document/{DOCUMENT_A}/stored_document"
METADATA = StoredObjectMetadata(
    storage_key=KEY,
    media_type="application/pdf",
    size_bytes=41,
    sha256="a" * 64,
)


async def _reset_database(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(metadata.drop_all)
        await connection.run_sync(metadata.create_all)
        await connection.execute(
            insert(workspaces).values(
                id=WORKSPACE_ID,
                name="Artifact workspace",
                is_local=False,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await connection.execute(
            insert(local_sessions).values(
                token_digest=OWNER_DIGEST,
                workspace_id=WORKSPACE_ID,
                created_at=NOW,
                last_seen_at=NOW,
                revoked_at=None,
            )
        )
        await connection.execute(
            insert(documents),
            (
                {
                    "id": DOCUMENT_A,
                    "workspace_id": WORKSPACE_ID,
                    "sha256": "1" * 64,
                    "document_type": "pdf",
                    "media_type": "application/pdf",
                    "size_bytes": 41,
                    "safe_filename": "a.pdf",
                    "created_at": NOW,
                    "deleted_at": None,
                },
                {
                    "id": DOCUMENT_B,
                    "workspace_id": WORKSPACE_ID,
                    "sha256": "2" * 64,
                    "document_type": "pdf",
                    "media_type": "application/pdf",
                    "size_bytes": 41,
                    "safe_filename": "b.pdf",
                    "created_at": NOW,
                    "deleted_at": None,
                },
            ),
        )
        await connection.execute(
            insert(discovery_analysis_sessions).values(
                id=SESSION_ID,
                workspace_id=WORKSPACE_ID,
                owner_session_digest=OWNER_DIGEST,
                created_by_user_id=None,
                query="tables",
                document_types=["pdf"],
                include_domains=[],
                exclude_domains=[],
                tables_required=True,
                provider_search_ids=[],
                status="completed",
                job_stage="completed",
                creation_reason="initial",
                candidate_count=1,
                session_byte_limit=1024,
                bytes_downloaded=0,
                cancellation_requested=False,
                error_code=None,
                error_detail=None,
                created_at=NOW,
                updated_at=NOW,
                expires_at=NOW + timedelta(hours=1),
                completed_at=NOW,
            )
        )
        await connection.execute(
            insert(candidate_analyses).values(
                id=CANDIDATE_ID,
                workspace_id=WORKSPACE_ID,
                session_id=SESSION_ID,
                ordinal=0,
                source_url="https://example.invalid/a.pdf",
                title="A",
                description=None,
                document_type="pdf",
                status="ready",
                attempt_count=1,
                available_at=NOW,
                claimed_by=None,
                lease_expires_at=None,
                bytes_downloaded=0,
                content_length=None,
                sha256=None,
                media_type=None,
                safe_filename=None,
                page_count=1,
                analyzed_page_count=1,
                table_count=1,
                table_count_lower_bound=False,
                preview_page_num=1,
                preview_width=100,
                preview_height=100,
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
    engine = create_async_engine(database_url, pool_size=5, max_overflow=5)
    try:
        await _reset_database(engine)
        yield engine
    finally:
        await engine.dispose()


async def test_available_transition_and_reference_increment_usage_once(
    database_engine: AsyncEngine,
) -> None:
    repository = PostgresArtifactRepository(database_engine)
    upload = await repository.begin_upload(WORKSPACE_ID, KEY, METADATA, now=NOW)

    first = await repository.finalize_reference(
        upload.id,
        workspace_id=WORKSPACE_ID,
        document_id=DOCUMENT_A,
        candidate_analysis_id=None,
        kind=ArtifactKind.STORED_DOCUMENT,
        lifecycle=ArtifactLifecycle.PERSISTENT,
        expires_at=None,
        now=NOW,
    )
    second = await repository.finalize_reference(
        upload.id,
        workspace_id=WORKSPACE_ID,
        document_id=DOCUMENT_A,
        candidate_analysis_id=None,
        kind=ArtifactKind.STORED_DOCUMENT,
        lifecycle=ArtifactLifecycle.PERSISTENT,
        expires_at=None,
        now=NOW + timedelta(seconds=1),
    )

    usage = await repository.get_usage(WORKSPACE_ID)
    resolved = await repository.resolve_document_object(WORKSPACE_ID, DOCUMENT_A)
    assert first.id == second.id
    assert resolved is not None
    assert resolved.id == upload.id
    assert resolved.state is ArtifactObjectState.AVAILABLE
    assert (usage.retained_bytes, usage.retained_objects) == (METADATA.size_bytes, 1)


async def test_discarded_upload_is_deleted_without_charging_usage(
    database_engine: AsyncEngine,
) -> None:
    repository = PostgresArtifactRepository(database_engine)
    upload = await repository.begin_upload(WORKSPACE_ID, KEY, METADATA, now=NOW)

    discarded = await repository.discard_upload(upload.id, WORKSPACE_ID, now=NOW)
    claims = await repository.claim_deletions(
        "worker-a",
        now=NOW,
        lease_expires_at=NOW + timedelta(minutes=1),
        limit=10,
    )
    assert discarded is not None
    assert discarded.state is ArtifactObjectState.DELETING
    assert discarded.available_at is None
    assert [claim.id for claim in claims] == [upload.id]
    assert await repository.complete_deletion(upload.id, WORKSPACE_ID, "worker-a", now=NOW)

    usage = await repository.get_usage(WORKSPACE_ID)
    assert (usage.retained_bytes, usage.retained_objects) == (0, 0)


async def test_conflicting_owner_reference_discards_duplicate_upload(
    database_engine: AsyncEngine,
) -> None:
    repository = PostgresArtifactRepository(database_engine)
    original = await repository.begin_upload(WORKSPACE_ID, KEY, METADATA, now=NOW)
    original_reference = await repository.finalize_reference(
        original.id,
        workspace_id=WORKSPACE_ID,
        document_id=DOCUMENT_A,
        candidate_analysis_id=None,
        kind=ArtifactKind.STORED_DOCUMENT,
        lifecycle=ArtifactLifecycle.PERSISTENT,
        expires_at=None,
        now=NOW,
    )
    duplicate_key = f"workspaces/{WORKSPACE_ID}/document/{DOCUMENT_B}/stored_document"
    duplicate_metadata = StoredObjectMetadata(
        storage_key=duplicate_key,
        media_type="application/pdf",
        size_bytes=41,
        sha256="a" * 64,
    )
    duplicate = await repository.begin_upload(
        WORKSPACE_ID,
        duplicate_key,
        duplicate_metadata,
        now=NOW + timedelta(seconds=1),
    )

    winning_reference = await repository.finalize_reference(
        duplicate.id,
        workspace_id=WORKSPACE_ID,
        document_id=DOCUMENT_A,
        candidate_analysis_id=None,
        kind=ArtifactKind.STORED_DOCUMENT,
        lifecycle=ArtifactLifecycle.PERSISTENT,
        expires_at=None,
        now=NOW + timedelta(seconds=1),
    )

    usage = await repository.get_usage(WORKSPACE_ID)
    claims = await repository.claim_deletions(
        "worker-a",
        now=NOW + timedelta(seconds=1),
        lease_expires_at=NOW + timedelta(minutes=1),
        limit=10,
    )
    assert winning_reference.id == original_reference.id
    assert (usage.retained_bytes, usage.retained_objects) == (41, 1)
    assert [claim.id for claim in claims] == [duplicate.id]


async def test_expiry_deletion_retry_and_lease_keep_bytes_until_success(
    database_engine: AsyncEngine,
) -> None:
    repository = PostgresArtifactRepository(database_engine)
    preview_key = f"workspaces/{WORKSPACE_ID}/analysis/{CANDIDATE_ID}/preview_png"
    preview_metadata = StoredObjectMetadata(
        storage_key=preview_key,
        media_type="image/png",
        size_bytes=17,
        sha256="b" * 64,
    )
    upload = await repository.begin_upload(
        WORKSPACE_ID,
        preview_key,
        preview_metadata,
        now=NOW,
    )
    await repository.finalize_reference(
        upload.id,
        workspace_id=WORKSPACE_ID,
        document_id=None,
        candidate_analysis_id=CANDIDATE_ID,
        kind=ArtifactKind.PREVIEW_PNG,
        lifecycle=ArtifactLifecycle.TEMPORARY,
        expires_at=NOW + timedelta(minutes=5),
        now=NOW,
    )

    removed = await repository.claim_expired_references(
        now=NOW + timedelta(minutes=5),
        limit=10,
    )
    usage_while_deleting = await repository.get_usage(WORKSPACE_ID)
    assert len(removed) == 1
    assert usage_while_deleting.retained_bytes == 17

    first_claim = await repository.claim_deletions(
        "worker-a",
        now=NOW + timedelta(minutes=5),
        lease_expires_at=NOW + timedelta(minutes=6),
        limit=10,
    )
    blocked_claim = await repository.claim_deletions(
        "worker-b",
        now=NOW + timedelta(minutes=5, seconds=30),
        lease_expires_at=NOW + timedelta(minutes=7),
        limit=10,
    )
    assert [claim.id for claim in first_claim] == [upload.id]
    assert blocked_claim == ()

    retry_at = NOW + timedelta(minutes=8)
    assert await repository.fail_deletion(
        upload.id,
        WORKSPACE_ID,
        "worker-a",
        error_code="provider_unavailable",
        retry_at=retry_at,
        now=NOW + timedelta(minutes=6),
    )
    assert (
        await repository.claim_deletions(
            "worker-b",
            now=retry_at - timedelta(seconds=1),
            lease_expires_at=retry_at + timedelta(minutes=1),
            limit=10,
        )
        == ()
    )
    retry_claim = await repository.claim_deletions(
        "worker-b",
        now=retry_at,
        lease_expires_at=retry_at + timedelta(minutes=1),
        limit=10,
    )
    assert [claim.id for claim in retry_claim] == [upload.id]
    assert await repository.complete_deletion(
        upload.id,
        WORKSPACE_ID,
        "worker-b",
        now=retry_at,
    )
    usage = await repository.get_usage(WORKSPACE_ID)
    assert (usage.retained_bytes, usage.retained_objects) == (0, 0)


async def test_orphan_legacy_resolution_and_usage_reconciliation(
    database_engine: AsyncEngine,
) -> None:
    repository = PostgresArtifactRepository(database_engine)
    orphan = await repository.begin_upload(WORKSPACE_ID, KEY, METADATA, now=NOW)
    claimed_orphans = await repository.claim_orphan_objects(
        now=NOW + timedelta(hours=2),
        orphaned_before=NOW + timedelta(hours=1),
        limit=10,
    )
    assert [claim.id for claim in claimed_orphans] == [orphan.id]

    legacy_id = uuid4()
    legacy_key = f"workspaces/{WORKSPACE_ID}/analysis/{CANDIDATE_ID}/source_pdf"
    async with database_engine.begin() as connection:
        await connection.execute(
            insert(artifact_objects).values(
                id=legacy_id,
                workspace_id=WORKSPACE_ID,
                storage_key=legacy_key,
                media_type="application/pdf",
                size_bytes=None,
                sha256=None,
                state="legacy_pending",
                available_at=None,
                delete_attempt_count=0,
                delete_available_at=None,
                delete_claimed_by=None,
                delete_lease_expires_at=None,
                failure_code=None,
                created_at=NOW,
                updated_at=NOW,
                deleted_at=None,
            )
        )
        await connection.execute(
            insert(artifact_references).values(
                id=uuid4(),
                workspace_id=WORKSPACE_ID,
                artifact_object_id=legacy_id,
                candidate_analysis_id=CANDIDATE_ID,
                document_id=None,
                kind="source_pdf",
                lifecycle="temporary",
                expires_at=NOW + timedelta(hours=1),
                removed_at=None,
                created_at=NOW,
            )
        )
    legacy_claims = await repository.claim_legacy_pending(
        "worker-a",
        now=NOW,
        lease_expires_at=NOW + timedelta(minutes=1),
        limit=10,
    )
    assert [claim.id for claim in legacy_claims] == [legacy_id]
    retry_at = NOW + timedelta(minutes=2)
    assert await repository.fail_legacy_metadata(
        legacy_id,
        WORKSPACE_ID,
        "worker-a",
        error_code="storage_unavailable",
        retry_at=retry_at,
        now=NOW + timedelta(seconds=30),
    )
    assert (
        await repository.claim_legacy_pending(
            "worker-b",
            now=retry_at - timedelta(seconds=1),
            lease_expires_at=retry_at + timedelta(minutes=1),
            limit=10,
        )
        == ()
    )
    retried_legacy = await repository.claim_legacy_pending(
        "worker-b",
        now=retry_at,
        lease_expires_at=retry_at + timedelta(minutes=1),
        limit=10,
    )
    assert [claim.id for claim in retried_legacy] == [legacy_id]
    assert retried_legacy[0].failure_code == "storage_unavailable"
    measured = StoredObjectMetadata(
        storage_key=legacy_key,
        media_type="application/pdf",
        size_bytes=23,
        sha256="c" * 64,
    )
    assert await repository.complete_legacy_metadata(
        legacy_id,
        WORKSPACE_ID,
        "worker-b",
        measured,
        now=retry_at,
    )
    assert await repository.count_live_legacy_pending(WORKSPACE_ID) == 0

    async with database_engine.begin() as connection:
        await connection.execute(
            update(workspace_storage_usage)
            .where(workspace_storage_usage.c.workspace_id == WORKSPACE_ID)
            .values(retained_bytes=999, retained_objects=999)
        )
    reconciled = await repository.reconcile_usage(WORKSPACE_ID, now=NOW)
    assert (reconciled.retained_bytes, reconciled.retained_objects) == (23, 1)


async def test_expired_unmeasured_legacy_object_deletes_without_usage_decrement(
    database_engine: AsyncEngine,
) -> None:
    repository = PostgresArtifactRepository(database_engine)
    legacy_id = uuid4()
    legacy_key = f"workspaces/{WORKSPACE_ID}/analysis/{CANDIDATE_ID}/converted_pdf"
    async with database_engine.begin() as connection:
        await connection.execute(
            insert(artifact_objects).values(
                id=legacy_id,
                workspace_id=WORKSPACE_ID,
                storage_key=legacy_key,
                media_type="application/pdf",
                size_bytes=None,
                sha256=None,
                state="legacy_pending",
                available_at=None,
                delete_attempt_count=0,
                delete_available_at=None,
                delete_claimed_by=None,
                delete_lease_expires_at=None,
                failure_code=None,
                created_at=NOW,
                updated_at=NOW,
                deleted_at=None,
            )
        )
        await connection.execute(
            insert(artifact_references).values(
                id=uuid4(),
                workspace_id=WORKSPACE_ID,
                artifact_object_id=legacy_id,
                candidate_analysis_id=CANDIDATE_ID,
                document_id=None,
                kind="converted_pdf",
                lifecycle="temporary",
                expires_at=NOW,
                removed_at=None,
                created_at=NOW,
            )
        )

    usage_before = await repository.get_usage(WORKSPACE_ID)
    expired = await repository.claim_expired_references(now=NOW, limit=1)
    deletions = await repository.claim_deletions(
        "worker-a",
        now=NOW,
        lease_expires_at=NOW + timedelta(minutes=1),
        limit=1,
    )

    assert [reference.artifact_object_id for reference in expired] == [legacy_id]
    assert [artifact.id for artifact in deletions] == [legacy_id]
    assert await repository.complete_deletion(
        legacy_id,
        WORKSPACE_ID,
        "worker-a",
        now=NOW,
    )
    usage_after = await repository.get_usage(WORKSPACE_ID)
    remaining = await database_engine.connect()
    try:
        legacy_exists = await remaining.scalar(
            select(artifact_objects.c.id).where(artifact_objects.c.id == legacy_id)
        )
    finally:
        await remaining.close()
    assert (usage_before.retained_bytes, usage_before.retained_objects) == (0, 0)
    assert (usage_after.retained_bytes, usage_after.retained_objects) == (0, 0)
    assert legacy_exists is None


async def test_concurrent_new_reference_prevents_last_reference_deletion(
    database_engine: AsyncEngine,
) -> None:
    repository = PostgresArtifactRepository(database_engine)
    upload = await repository.begin_upload(WORKSPACE_ID, KEY, METADATA, now=NOW)
    await repository.finalize_reference(
        upload.id,
        workspace_id=WORKSPACE_ID,
        document_id=DOCUMENT_A,
        candidate_analysis_id=None,
        kind=ArtifactKind.STORED_DOCUMENT,
        lifecycle=ArtifactLifecycle.PERSISTENT,
        expires_at=None,
        now=NOW,
    )

    reference_inserted = anyio.Event()
    allow_commit = anyio.Event()
    removal_started = anyio.Event()
    removal_result: list[object] = []

    async def add_reference_while_holding_object_lock() -> None:
        async with database_engine.begin() as connection:
            await repository.finalize_reference_in_connection(
                connection,
                upload.id,
                workspace_id=WORKSPACE_ID,
                document_id=DOCUMENT_B,
                candidate_analysis_id=None,
                kind=ArtifactKind.STORED_DOCUMENT,
                lifecycle=ArtifactLifecycle.PERSISTENT,
                expires_at=None,
                now=NOW + timedelta(seconds=1),
            )
            reference_inserted.set()
            await allow_commit.wait()

    async def remove_first_reference() -> None:
        await reference_inserted.wait()
        removal_started.set()
        removal_result.append(
            await repository.remove_document_reference(
                WORKSPACE_ID,
                DOCUMENT_A,
                now=NOW + timedelta(seconds=2),
            )
        )

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(add_reference_while_holding_object_lock)
        task_group.start_soon(remove_first_reference)
        await reference_inserted.wait()
        await removal_started.wait()
        await anyio.lowlevel.checkpoint()
        allow_commit.set()

    assert removal_result
    resolved = await repository.resolve_document_object(WORKSPACE_ID, DOCUMENT_B)
    assert resolved is not None
    assert resolved.state is ArtifactObjectState.AVAILABLE
    async with database_engine.connect() as connection:
        state = await connection.scalar(
            select(artifact_objects.c.state).where(artifact_objects.c.id == upload.id)
        )
    assert state == "available"
