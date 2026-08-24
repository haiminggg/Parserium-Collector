import asyncio
from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine

from parserium_collector.adapters.database.engine import create_engine
from parserium_collector.adapters.database.tables import worker_heartbeats
from parserium_collector.settings import Settings


async def write_heartbeat(engine: AsyncEngine, worker_id: str) -> None:
    statement = insert(worker_heartbeats).values(
        worker_id=worker_id,
        last_seen_at=datetime.now(UTC),
    )
    statement = statement.on_conflict_do_update(
        index_elements=[worker_heartbeats.c.worker_id],
        set_={"last_seen_at": statement.excluded.last_seen_at},
    )
    async with engine.begin() as connection:
        await connection.execute(statement)


async def run() -> None:
    settings = Settings()
    engine = create_engine(settings)
    try:
        while True:
            await write_heartbeat(engine, settings.worker_id)
            await asyncio.sleep(settings.worker_heartbeat_seconds)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(run())
