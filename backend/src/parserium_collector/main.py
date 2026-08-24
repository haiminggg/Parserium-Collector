from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from parserium_collector.adapters.database.engine import create_engine
from parserium_collector.adapters.firecrawl.health import firecrawl_health_probe
from parserium_collector.features.health.probes import database_probe, storage_probe, worker_probe
from parserium_collector.features.health.router import router as health_router
from parserium_collector.features.health.service import HealthService, unavailable_health_service
from parserium_collector.settings import Settings


def create_app(
    settings: Settings | None = None,
    health_service: HealthService | None = None,
) -> FastAPI:
    resolved = settings or Settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        engine = None
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
        yield
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
    app.include_router(health_router)
    if resolved.static_root.is_dir():
        app.mount("/", StaticFiles(directory=resolved.static_root, html=True), name="web")
    return app


app = create_app()
