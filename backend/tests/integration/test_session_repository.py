import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from sqlalchemy import URL, insert
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from parserium_collector.adapters.database.tables import metadata, workspaces
from parserium_collector.features.session.repository import PostgresSessionRepository

LOCAL_WORKSPACE_ID = UUID("10000000-0000-4000-8000-000000000001")


def _database_url() -> str:
    configured = os.environ.get("TEST_DATABASE_URL")
    if configured is not None:
        return configured
    password_file = Path(os.environ["TEST_DATABASE_PASSWORD_FILE"])
    return URL.create(
        "postgresql+psycopg",
        username="parserium_collector",
        password=password_file.read_text(encoding="utf-8").strip(),
        host=os.environ["TEST_DATABASE_HOST"],
        port=5432,
        database="parserium_collector",
    ).render_as_string(hide_password=False)


async def _reset_schema_with_local_workspace(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(metadata.drop_all)
        await connection.run_sync(metadata.create_all)
        await connection.execute(
            insert(workspaces).values(
                id=LOCAL_WORKSPACE_ID,
                name="Local workspace",
                is_local=True,
                created_at=datetime(2026, 8, 24, 12, 0, tzinfo=UTC),
                updated_at=datetime(2026, 8, 24, 12, 0, tzinfo=UTC),
            )
        )


async def test_pairing_exchange_and_session_lifecycle_are_transactional() -> None:
    engine = create_async_engine(_database_url())
    try:
        await _reset_schema_with_local_workspace(engine)
        repository = PostgresSessionRepository(engine)
        now = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)

        await repository.replace_pairing_code(
            "a" * 64,
            now,
            now + timedelta(minutes=10),
        )

        assert await repository.has_active_session(now - timedelta(hours=24)) is False
        exchanged = await repository.exchange_pairing_code("a" * 64, "b" * 64, now)
        assert exchanged is not None
        assert exchanged.workspace_id == LOCAL_WORKSPACE_ID
        assert await repository.has_active_session(now - timedelta(hours=24)) is True
        assert await repository.exchange_pairing_code("a" * 64, "c" * 64, now) is None
        session = await repository.load_session("b" * 64)
        assert session is not None
        assert session.workspace_id == LOCAL_WORKSPACE_ID
        assert session.workspace_name == "Local workspace"
        assert session.last_seen_at == now
        assert session.revoked_at is None

        touched_at = now + timedelta(minutes=1)
        await repository.touch_session("b" * 64, touched_at)
        touched = await repository.load_session("b" * 64)
        assert touched is not None
        assert touched.last_seen_at == touched_at

        revoked_at = now + timedelta(minutes=2)
        await repository.revoke_session("b" * 64, revoked_at)
        revoked = await repository.load_session("b" * 64)
        assert revoked is not None
        assert revoked.revoked_at == revoked_at
        assert await repository.has_active_session(now - timedelta(hours=24)) is False
    finally:
        await engine.dispose()


async def test_expired_or_replaced_pairing_code_cannot_be_exchanged() -> None:
    engine = create_async_engine(_database_url())
    try:
        await _reset_schema_with_local_workspace(engine)
        repository = PostgresSessionRepository(engine)
        now = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)

        await repository.replace_pairing_code("a" * 64, now, now + timedelta(minutes=1))
        assert await repository.has_active_pairing(now) is True
        await repository.replace_pairing_code("b" * 64, now, now + timedelta(minutes=10))

        assert await repository.exchange_pairing_code("a" * 64, "c" * 64, now) is None
        assert (
            await repository.exchange_pairing_code(
                "b" * 64,
                "d" * 64,
                now + timedelta(minutes=10),
            )
            is None
        )
        assert await repository.has_active_pairing(now + timedelta(minutes=10)) is False
    finally:
        await engine.dispose()


async def test_recovery_revokes_all_sessions_and_replaces_pairing_atomically() -> None:
    engine = create_async_engine(_database_url())
    try:
        await _reset_schema_with_local_workspace(engine)
        repository = PostgresSessionRepository(engine)
        now = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)
        await repository.replace_pairing_code("a" * 64, now, now + timedelta(minutes=10))
        assert await repository.exchange_pairing_code("a" * 64, "b" * 64, now) is not None

        recovered_at = now + timedelta(minutes=1)
        await repository.revoke_all_and_replace_pairing(
            "c" * 64,
            recovered_at,
            recovered_at + timedelta(minutes=10),
        )

        assert await repository.has_active_session(now - timedelta(hours=24)) is False
        assert await repository.exchange_pairing_code("a" * 64, "d" * 64, recovered_at) is None
        assert await repository.exchange_pairing_code("c" * 64, "e" * 64, recovered_at) is not None
    finally:
        await engine.dispose()
