import os
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from parserium_collector.adapters.database.tables import metadata
from parserium_collector.features.health.models import ComponentStatus
from parserium_collector.features.health.probes import database_probe, worker_probe
from parserium_collector.worker import write_heartbeat


def database_url() -> str:
    try:
        return os.environ["TEST_DATABASE_URL"]
    except KeyError as error:
        raise RuntimeError("TEST_DATABASE_URL is required for integration tests.") from error


async def prepare_database(engine: AsyncEngine, revision: str = "0001_foundation") -> None:
    async with engine.begin() as connection:
        await connection.run_sync(metadata.drop_all)
        await connection.run_sync(metadata.create_all)
        await connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
        await connection.execute(
            text("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)")
        )
        await connection.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
            {"revision": revision},
        )


async def test_database_probe_accepts_matching_migration() -> None:
    engine = create_async_engine(database_url())
    try:
        await prepare_database(engine)

        result = await database_probe(engine, "0001_foundation")()

        assert result.status is ComponentStatus.AVAILABLE
    finally:
        await engine.dispose()


async def test_database_probe_rejects_mismatched_migration() -> None:
    engine = create_async_engine(database_url())
    try:
        await prepare_database(engine, revision="unexpected")

        result = await database_probe(engine, "0001_foundation")()

        assert result.status is ComponentStatus.UNAVAILABLE
        assert result.detail == "Database migration does not match the application."
    finally:
        await engine.dispose()


async def test_worker_probe_accepts_fresh_heartbeat_and_rejects_stale_heartbeat() -> None:
    engine = create_async_engine(database_url())
    try:
        await prepare_database(engine)
        await write_heartbeat(engine, "integration-worker")

        fresh = await worker_probe(engine, stale_seconds=30)()

        assert fresh.status is ComponentStatus.AVAILABLE

        stale_at = datetime.now(UTC) - timedelta(minutes=2)
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE worker_heartbeats SET last_seen_at = :stale_at "
                    "WHERE worker_id = :worker_id"
                ),
                {"stale_at": stale_at, "worker_id": "integration-worker"},
            )

        stale = await worker_probe(engine, stale_seconds=30)()

        assert stale.status is ComponentStatus.UNAVAILABLE
        assert stale.detail == "No fresh worker heartbeat."
    finally:
        await engine.dispose()
