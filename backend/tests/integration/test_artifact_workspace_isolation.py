from datetime import timedelta
from uuid import UUID

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncEngine
from test_artifact_repository import (
    DOCUMENT_A,
    KEY,
    METADATA,
    NOW,
    WORKSPACE_ID,
)
from test_artifact_repository import database_engine as database_engine

from parserium_collector.adapters.database.tables import documents, workspaces
from parserium_collector.features.storage.models import (
    ArtifactKind,
    ArtifactLifecycle,
    StoredObjectMetadata,
)
from parserium_collector.features.storage.repository import PostgresArtifactRepository

WORKSPACE_B = UUID("70000000-0000-4000-8000-000000000002")
DOCUMENT_B = UUID("71000000-0000-4000-8000-000000000003")


async def _seed_workspace_b(database_engine: AsyncEngine) -> None:
    async with database_engine.begin() as connection:
        await connection.execute(
            insert(workspaces).values(
                id=WORKSPACE_B,
                name="Workspace B",
                is_local=False,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await connection.execute(
            insert(documents).values(
                id=DOCUMENT_B,
                workspace_id=WORKSPACE_B,
                sha256="3" * 64,
                document_type="pdf",
                media_type="application/pdf",
                size_bytes=41,
                safe_filename="workspace-b.pdf",
                created_at=NOW,
                deleted_at=None,
            )
        )


async def test_same_storage_key_is_isolated_per_workspace(
    database_engine: AsyncEngine,
) -> None:
    await _seed_workspace_b(database_engine)
    repository = PostgresArtifactRepository(database_engine)
    upload_a = await repository.begin_upload(WORKSPACE_ID, KEY, METADATA, now=NOW)
    metadata_b = StoredObjectMetadata(
        storage_key=KEY,
        media_type=METADATA.media_type,
        size_bytes=METADATA.size_bytes,
        sha256="d" * 64,
    )
    upload_b = await repository.begin_upload(WORKSPACE_B, KEY, metadata_b, now=NOW)

    await repository.finalize_reference(
        upload_a.id,
        workspace_id=WORKSPACE_ID,
        document_id=DOCUMENT_A,
        candidate_analysis_id=None,
        kind=ArtifactKind.STORED_DOCUMENT,
        lifecycle=ArtifactLifecycle.PERSISTENT,
        expires_at=None,
        now=NOW,
    )
    await repository.finalize_reference(
        upload_b.id,
        workspace_id=WORKSPACE_B,
        document_id=DOCUMENT_B,
        candidate_analysis_id=None,
        kind=ArtifactKind.STORED_DOCUMENT,
        lifecycle=ArtifactLifecycle.PERSISTENT,
        expires_at=None,
        now=NOW,
    )

    assert upload_a.id != upload_b.id
    assert await repository.resolve_document_object(WORKSPACE_ID, DOCUMENT_B) is None
    assert await repository.resolve_document_object(WORKSPACE_B, DOCUMENT_A) is None
    assert (
        await repository.remove_document_reference(
            WORKSPACE_B,
            DOCUMENT_A,
            now=NOW + timedelta(seconds=1),
        )
        is None
    )
    usage_a = await repository.get_usage(WORKSPACE_ID)
    usage_b = await repository.get_usage(WORKSPACE_B)
    assert (usage_a.retained_bytes, usage_a.retained_objects) == (41, 1)
    assert (usage_b.retained_bytes, usage_b.retained_objects) == (41, 1)
