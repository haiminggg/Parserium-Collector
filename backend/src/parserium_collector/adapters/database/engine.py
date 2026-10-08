from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from parserium_collector.settings import Settings


def create_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        settings.database_url(),
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
    )
