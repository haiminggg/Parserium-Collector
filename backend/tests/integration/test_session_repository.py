import os
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import create_async_engine

from parserium_collector.adapters.database.tables import metadata
from parserium_collector.features.session.repository import PostgresSessionRepository


async def test_pairing_exchange_and_session_lifecycle_are_transactional() -> None:
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    try:
        async with engine.begin() as connection:
            await connection.run_sync(metadata.drop_all)
            await connection.run_sync(metadata.create_all)
        repository = PostgresSessionRepository(engine)
        now = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)

        await repository.replace_pairing_code(
            "a" * 64,
            now,
            now + timedelta(minutes=10),
        )

        assert await repository.has_active_session(now - timedelta(hours=24)) is False
        assert await repository.exchange_pairing_code("a" * 64, "b" * 64, now) is True
        assert await repository.has_active_session(now - timedelta(hours=24)) is True
        assert await repository.exchange_pairing_code("a" * 64, "c" * 64, now) is False
        session = await repository.load_session("b" * 64)
        assert session is not None
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
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    try:
        async with engine.begin() as connection:
            await connection.run_sync(metadata.drop_all)
            await connection.run_sync(metadata.create_all)
        repository = PostgresSessionRepository(engine)
        now = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)

        await repository.replace_pairing_code("a" * 64, now, now + timedelta(minutes=1))
        assert await repository.has_active_pairing(now) is True
        await repository.replace_pairing_code("b" * 64, now, now + timedelta(minutes=10))

        assert await repository.exchange_pairing_code("a" * 64, "c" * 64, now) is False
        assert (
            await repository.exchange_pairing_code(
                "b" * 64,
                "d" * 64,
                now + timedelta(minutes=10),
            )
            is False
        )
        assert await repository.has_active_pairing(now + timedelta(minutes=10)) is False
    finally:
        await engine.dispose()


async def test_recovery_revokes_all_sessions_and_replaces_pairing_atomically() -> None:
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    try:
        async with engine.begin() as connection:
            await connection.run_sync(metadata.drop_all)
            await connection.run_sync(metadata.create_all)
        repository = PostgresSessionRepository(engine)
        now = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)
        await repository.replace_pairing_code("a" * 64, now, now + timedelta(minutes=10))
        assert await repository.exchange_pairing_code("a" * 64, "b" * 64, now) is True

        recovered_at = now + timedelta(minutes=1)
        await repository.revoke_all_and_replace_pairing(
            "c" * 64,
            recovered_at,
            recovered_at + timedelta(minutes=10),
        )

        assert await repository.has_active_session(now - timedelta(hours=24)) is False
        assert await repository.exchange_pairing_code("a" * 64, "d" * 64, recovered_at) is False
        assert await repository.exchange_pairing_code("c" * 64, "e" * 64, recovered_at) is True
    finally:
        await engine.dispose()
