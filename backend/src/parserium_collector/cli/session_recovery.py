import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from parserium_collector.adapters.database.engine import create_engine
from parserium_collector.features.session.repository import PostgresSessionRepository
from parserium_collector.features.session.service import SessionService
from parserium_collector.settings import Settings


async def run_recovery(
    service: SessionService,
    now: datetime,
    write_line: Callable[[str], None] = print,
) -> None:
    pairing = await service.recover(now)
    write_line(f"LOCAL PAIRING CODE: {pairing.code}")
    write_line(f"EXPIRES AT: {pairing.expires_at.isoformat()}")


async def _main() -> None:
    settings = Settings()
    engine = create_engine(settings)
    service = SessionService(
        repository=PostgresSessionRepository(engine),
        signing_secret=settings.session_signing_secret(),
        pairing_ttl=timedelta(seconds=settings.pairing_ttl_seconds),
        session_idle_ttl=timedelta(seconds=settings.session_idle_seconds),
    )
    try:
        await run_recovery(service, datetime.now(UTC))
    finally:
        await engine.dispose()


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
