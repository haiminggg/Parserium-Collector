import base64
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import anyio
import pytest
from sqlalchemy import URL, insert
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from parserium_collector.adapters.database.tables import metadata, users, workspaces
from parserium_collector.features.firecrawl_connections.crypto import (
    CredentialVault,
    FileKeyProvider,
)
from parserium_collector.features.firecrawl_connections.errors import (
    ConnectionDefaultConflictError,
    ConnectionNameConflictError,
)
from parserium_collector.features.firecrawl_connections.models import (
    ConnectionStatus,
    ConnectionType,
    ConnectionUpdate,
    NewConnection,
    RewrappedEnvelope,
    ValidationOutcome,
)
from parserium_collector.features.firecrawl_connections.repository import (
    PostgresFirecrawlConnectionRepository,
)
from parserium_collector.features.identity.models import WorkspaceRole, WorkspaceScope

NOW = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
WORKSPACE_A = UUID("10000000-0000-4000-8000-000000000014")
WORKSPACE_B = UUID("10000000-0000-4000-8000-000000000024")
USER_A = UUID("20000000-0000-4000-8000-000000000014")
USER_B = UUID("20000000-0000-4000-8000-000000000024")
CONNECTION_A = UUID("30000000-0000-4000-8000-000000000014")
CONNECTION_B = UUID("30000000-0000-4000-8000-000000000024")
CONNECTION_C = UUID("30000000-0000-4000-8000-000000000034")
SCOPE_A = WorkspaceScope(WORKSPACE_A, USER_A, WorkspaceRole.OWNER)
SCOPE_B = WorkspaceScope(WORKSPACE_B, USER_B, WorkspaceRole.OWNER)


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
        await connection.execute(
            insert(users),
            [
                {
                    "id": USER_A,
                    "email": "owner-a@parserium.test",
                    "normalized_email": "owner-a@parserium.test",
                    "display_name": "Owner A",
                    "created_at": NOW,
                    "updated_at": NOW,
                    "disabled_at": None,
                },
                {
                    "id": USER_B,
                    "email": "owner-b@parserium.test",
                    "normalized_email": "owner-b@parserium.test",
                    "display_name": "Owner B",
                    "created_at": NOW,
                    "updated_at": NOW,
                    "disabled_at": None,
                },
            ],
        )
        await connection.execute(
            insert(workspaces),
            [
                {
                    "id": WORKSPACE_A,
                    "name": "Workspace A",
                    "is_local": False,
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
    try:
        yield engine
    finally:
        async with engine.begin() as connection:
            await connection.run_sync(metadata.drop_all)
        await engine.dispose()


def vault(key_id: str = "test-key", key: bytes = b"k" * 32) -> CredentialVault:
    return CredentialVault(FileKeyProvider.from_bytes(key_id=key_id, key=key))


async def draft(
    connection_id: UUID,
    *,
    name: str,
    credential: str = "test-only-credential",
) -> NewConnection:
    envelope = await vault().encrypt(credential, WORKSPACE_A, connection_id, 1)
    return NewConnection(
        id=connection_id,
        name=name,
        normalized_name=name.casefold(),
        connection_type=ConnectionType.CLOUD,
        normalized_base_url=None,
        credential_envelope=envelope,
        credential_revision=1,
        enabled=True,
        is_default=False,
    )


async def make_healthy(
    repository: PostgresFirecrawlConnectionRepository,
    connection_id: UUID,
) -> None:
    recorded = await repository.record_validation(
        WORKSPACE_A,
        connection_id,
        1,
        ValidationOutcome(
            succeeded=True,
            capability_profile={"contract": "metadata-search-v2"},
            failure_category=None,
        ),
        NOW + timedelta(seconds=1),
    )
    assert recorded is not None
    assert recorded.status is ConnectionStatus.HEALTHY


async def test_worker_lookup_requires_exact_current_healthy_revision(
    database_engine: AsyncEngine,
) -> None:
    repository = PostgresFirecrawlConnectionRepository(database_engine)
    await repository.create_connection(
        SCOPE_A,
        await draft(CONNECTION_A, name="Worker connection"),
        NOW,
    )

    assert await repository.get_usable_connection_for_job(WORKSPACE_A, CONNECTION_A, 1) is None
    await make_healthy(repository, CONNECTION_A)

    exact = await repository.get_usable_connection_for_job(WORKSPACE_A, CONNECTION_A, 1)
    assert exact is not None
    assert exact.id == CONNECTION_A
    assert exact.credential_revision == 1
    assert await repository.get_usable_connection_for_job(WORKSPACE_A, CONNECTION_A, 2) is None
    assert await repository.get_usable_connection_for_job(WORKSPACE_B, CONNECTION_A, 1) is None

    disabled = await repository.update_metadata(
        SCOPE_A,
        CONNECTION_A,
        ConnectionUpdate(enabled=False),
        NOW + timedelta(seconds=2),
    )
    assert disabled is not None
    assert await repository.get_usable_connection_for_job(WORKSPACE_A, CONNECTION_A, 1) is None


async def test_repository_persists_validation_rotation_tombstone_and_rewrap(
    database_engine: AsyncEngine,
) -> None:
    repository = PostgresFirecrawlConnectionRepository(database_engine)
    created = await repository.create_connection(
        SCOPE_A,
        await draft(CONNECTION_A, name="Primary"),
        NOW,
    )

    assert created.status is ConnectionStatus.NEVER_VALIDATED
    assert await repository.get_connection(WORKSPACE_A, CONNECTION_A) == created
    assert await repository.list_connections(WORKSPACE_A) == (created,)
    await make_healthy(repository, CONNECTION_A)

    updated = await repository.update_metadata(
        SCOPE_A,
        CONNECTION_A,
        ConnectionUpdate(name="Primary Cloud", normalized_name="primary cloud"),
        NOW + timedelta(seconds=2),
    )
    assert updated is not None
    assert updated.name == "Primary Cloud"
    assert updated.updated_by_user_id == USER_A

    replacement = await vault().encrypt(
        "replacement-test-only",
        WORKSPACE_A,
        CONNECTION_A,
        2,
    )
    rotated = await repository.replace_credential(
        SCOPE_A,
        CONNECTION_A,
        replacement,
        1,
        NOW + timedelta(seconds=3),
    )
    assert rotated is not None
    assert rotated.credential_revision == 2
    assert rotated.status is ConnectionStatus.NEVER_VALIDATED
    assert (
        await repository.replace_credential(
            SCOPE_A,
            CONNECTION_A,
            replacement,
            1,
            NOW + timedelta(seconds=4),
        )
        is None
    )

    envelopes = await repository.list_envelopes_for_rewrap(None, 10)
    assert [record.id for record in envelopes] == [CONNECTION_A]
    current = envelopes[0].credential_envelope
    assert current is not None
    rewrapped = current.model_copy(
        update={
            "key_id": "replacement-key",
            "wrapped_data_key": base64.b64encode(b"w" * 40).decode("ascii"),
        }
    )
    assert (
        await repository.replace_wrapped_keys(
            (
                RewrappedEnvelope(
                    workspace_id=WORKSPACE_A,
                    connection_id=CONNECTION_A,
                    expected_key_id="test-key",
                    envelope=rewrapped,
                ),
            )
        )
        == 1
    )
    after_rewrap = await repository.get_connection(WORKSPACE_A, CONNECTION_A)
    assert after_rewrap is not None
    assert after_rewrap.credential_envelope is not None
    assert after_rewrap.credential_envelope.key_id == "replacement-key"

    assert await repository.tombstone(
        SCOPE_A,
        CONNECTION_A,
        NOW + timedelta(seconds=5),
    )
    assert await repository.get_connection(WORKSPACE_A, CONNECTION_A) is None
    assert await repository.list_connections(WORKSPACE_A) == ()
    assert await repository.list_envelopes_for_rewrap(None, 10) == ()


async def test_names_are_unique_per_workspace_and_defaults_remain_singular(
    database_engine: AsyncEngine,
) -> None:
    repository = PostgresFirecrawlConnectionRepository(database_engine)
    await repository.create_connection(
        SCOPE_A,
        await draft(CONNECTION_A, name="Shared"),
        NOW,
    )
    draft_b = await draft(CONNECTION_B, name="Shared")
    draft_b = NewConnection(
        **{
            **draft_b.__dict__,
            "credential_envelope": await vault().encrypt(
                "workspace-b-test-only",
                WORKSPACE_B,
                CONNECTION_B,
                1,
            ),
        }
    )
    await repository.create_connection(SCOPE_B, draft_b, NOW)
    with pytest.raises(ConnectionNameConflictError):
        await repository.create_connection(
            SCOPE_A,
            await draft(CONNECTION_C, name="Shared"),
            NOW,
        )

    await repository.create_connection(
        SCOPE_A,
        await draft(CONNECTION_C, name="Secondary"),
        NOW,
    )
    await make_healthy(repository, CONNECTION_A)
    await make_healthy(repository, CONNECTION_C)
    outcomes: list[object] = []

    async def select_default(connection_id: UUID) -> None:
        try:
            outcomes.append(
                await repository.update_metadata(
                    SCOPE_A,
                    connection_id,
                    ConnectionUpdate(is_default=True),
                    NOW + timedelta(seconds=2),
                )
            )
        except ConnectionDefaultConflictError as error:
            outcomes.append(error)

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(select_default, CONNECTION_A)
        task_group.start_soon(select_default, CONNECTION_C)

    defaults = [
        record for record in await repository.list_connections(WORKSPACE_A) if record.is_default
    ]
    assert len(outcomes) == 2
    assert len(defaults) == 1
