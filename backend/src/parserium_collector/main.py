import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import httpx
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from parserium_collector.adapters.database.engine import create_engine
from parserium_collector.adapters.firecrawl.client import FirecrawlClient
from parserium_collector.adapters.firecrawl.health import firecrawl_health_probe
from parserium_collector.features.discovery.router import router as discovery_router
from parserium_collector.features.discovery.service import DiscoveryService
from parserium_collector.features.health.probes import database_probe, storage_probe, worker_probe
from parserium_collector.features.health.router import router as health_router
from parserium_collector.features.health.service import HealthService, unavailable_health_service
from parserium_collector.features.session.repository import PostgresSessionRepository
from parserium_collector.features.session.router import router as session_router
from parserium_collector.features.session.service import SessionService
from parserium_collector.security import LocalRequestGuardMiddleware
from parserium_collector.settings import Settings

LOGGER = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    health_service: HealthService | None = None,
    session_service: SessionService | None = None,
    discovery_service: DiscoveryService | None = None,
) -> FastAPI:
    resolved = settings or Settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        engine = None
        firecrawl_http_client = None
        if health_service is not None:
            application.state.health_service = health_service
        else:
            try:
                engine = create_engine(resolved)
                application.state.health_service = HealthService(
                    resolved.build_id,
                    (
                        database_probe(engine, resolved.expected_migration),
                        storage_probe(
                            resolved.storage_root,
                            resolved.storage_reserve_bytes,
                            resolved.storage_reserve_ratio,
                        ),
                        worker_probe(engine, resolved.worker_stale_seconds),
                        firecrawl_health_probe(
                            str(resolved.firecrawl_base_url).rstrip("/")
                            if resolved.firecrawl_base_url is not None
                            else None,
                            resolved.firecrawl_timeout_seconds,
                        ),
                    ),
                )
            except (OSError, ValueError):
                application.state.health_service = unavailable_health_service(
                    resolved.build_id,
                    "Required installation settings are unavailable.",
                )
        if session_service is not None:
            application.state.session_service = session_service
        elif health_service is None and engine is not None:
            application.state.session_service = SessionService(
                repository=PostgresSessionRepository(engine),
                signing_secret=resolved.session_signing_secret(),
                pairing_ttl=timedelta(seconds=resolved.pairing_ttl_seconds),
                session_idle_ttl=timedelta(seconds=resolved.session_idle_seconds),
            )
        if hasattr(application.state, "session_service"):
            pairing = await application.state.session_service.ensure_pairing_code(datetime.now(UTC))
            if pairing is not None:
                LOGGER.warning(
                    "LOCAL PAIRING CODE: %s (expires at %s)",
                    pairing.code,
                    pairing.expires_at.isoformat(),
                )
        if discovery_service is not None:
            application.state.discovery_service = discovery_service
        elif resolved.firecrawl_base_url is not None:
            firecrawl_http_client = httpx.AsyncClient(
                base_url=str(resolved.firecrawl_base_url).rstrip("/"),
                timeout=httpx.Timeout(resolved.firecrawl_search_timeout_seconds),
            )
            application.state.discovery_service = DiscoveryService(
                FirecrawlClient(firecrawl_http_client)
            )
        else:
            application.state.discovery_service = None
        try:
            yield
        finally:
            if firecrawl_http_client is not None:
                await firecrawl_http_client.aclose()
            if engine is not None:
                await engine.dispose()

    app = FastAPI(
        title=resolved.app_name,
        version=resolved.release_version,
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.settings = resolved
    app.add_middleware(
        LocalRequestGuardMiddleware,
        allowed_hosts=resolved.allowed_hosts,
        allowed_origins=resolved.allowed_origins,
    )
    app.include_router(health_router)
    app.include_router(session_router)
    app.include_router(discovery_router)
    if resolved.static_root.is_dir():
        app.mount("/", StaticFiles(directory=resolved.static_root, html=True), name="web")
    return app


app = create_app()
