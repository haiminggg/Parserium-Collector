import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from typing import Protocol

import anyio
import httpx
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine

from parserium_collector.adapters.database.engine import create_engine
from parserium_collector.adapters.database.tables import worker_heartbeats
from parserium_collector.adapters.firecrawl.client import FirecrawlClient
from parserium_collector.features.acquisition.downloader import BoundedDownloader
from parserium_collector.features.acquisition.network_policy import NetworkPolicy
from parserium_collector.features.acquisition.repository import (
    PostgresAcquisitionRepository,
)
from parserium_collector.features.acquisition.validation import DocumentValidator
from parserium_collector.features.acquisition.worker import AcquisitionWorker
from parserium_collector.features.analysis.discovery_worker import DiscoveryJobWorker
from parserium_collector.features.analysis.docx import LibreOfficeDocxConverter
from parserium_collector.features.analysis.job_repository import PostgresDiscoveryJobRepository
from parserium_collector.features.analysis.parser import LiteParseAdapter
from parserium_collector.features.analysis.repository import (
    PostgresAnalysisRepository,
)
from parserium_collector.features.analysis.worker import AnalysisWorker
from parserium_collector.features.discovery.scoped import (
    HostedScopedDiscovery,
    ScopedDiscoveryService,
    SelfHostedScopedDiscovery,
)
from parserium_collector.features.discovery.service import DiscoveryService
from parserium_collector.features.firecrawl_connections.crypto import (
    CredentialVault,
    FileKeyProvider,
)
from parserium_collector.features.firecrawl_connections.executor import SecureFirecrawlExecutor
from parserium_collector.features.firecrawl_connections.repository import (
    PostgresFirecrawlConnectionRepository,
)
from parserium_collector.features.firecrawl_connections.service import FirecrawlConnectionService
from parserium_collector.features.storage.factory import create_storage_runtime
from parserium_collector.features.storage.maintenance import ArtifactMaintenanceService
from parserium_collector.features.storage.repository import PostgresArtifactRepository
from parserium_collector.settings import DeploymentMode, Settings

StopWaiter = Callable[[anyio.Event, float], Awaitable[bool]]
LOGGER = logging.getLogger(__name__)


class WorkerRunner(Protocol):
    async def run_once(self) -> bool: ...


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


async def wait_for_stop(stop: anyio.Event, interval: float) -> bool:
    with anyio.move_on_after(interval):
        await stop.wait()
    return stop.is_set()


async def serve_worker(
    *,
    analysis_worker: WorkerRunner,
    acquisition_worker: WorkerRunner,
    heartbeat: Callable[[], Awaitable[None]],
    heartbeat_seconds: float,
    idle_seconds: float,
    stop: anyio.Event,
    waiter: StopWaiter = wait_for_stop,
    maintenance_worker: WorkerRunner | None = None,
    maintenance_seconds: float = 30.0,
    discovery_workers: Sequence[WorkerRunner] = (),
) -> None:
    if heartbeat_seconds <= 0 or idle_seconds <= 0 or maintenance_seconds <= 0:
        raise ValueError("Worker polling intervals must be positive.")

    async def heartbeat_loop() -> None:
        while not stop.is_set():
            await heartbeat()
            if await waiter(stop, heartbeat_seconds):
                return

    async def acquisition_loop() -> None:
        while not stop.is_set():
            handled = await analysis_worker.run_once()
            if not handled:
                handled = await acquisition_worker.run_once()
            if not handled and await waiter(stop, idle_seconds):
                return
            if handled:
                await anyio.lowlevel.checkpoint()

    async def maintenance_loop(runner: WorkerRunner) -> None:
        while not stop.is_set():
            try:
                await runner.run_once()
            except Exception:
                LOGGER.warning("Artifact maintenance pass failed.")
            if await waiter(stop, maintenance_seconds):
                return

    async def discovery_loop(runner: WorkerRunner) -> None:
        while not stop.is_set():
            handled = await runner.run_once()
            if not handled and await waiter(stop, idle_seconds):
                return
            if handled:
                await anyio.lowlevel.checkpoint()

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(heartbeat_loop)
        task_group.start_soon(acquisition_loop)
        for discovery_worker in discovery_workers:
            task_group.start_soon(discovery_loop, discovery_worker)
        if maintenance_worker is not None:
            task_group.start_soon(maintenance_loop, maintenance_worker)


async def run(settings: Settings | None = None) -> None:
    settings = settings or Settings()
    engine = create_engine(settings)
    artifact_repository = PostgresArtifactRepository(engine)
    acquisition_repository = PostgresAcquisitionRepository(engine, artifact_repository)
    analysis_repository = PostgresAnalysisRepository(engine, artifact_repository)
    discovery_job_repository = PostgresDiscoveryJobRepository(engine)
    firecrawl_http_client: httpx.AsyncClient | None = None
    scoped_discovery: ScopedDiscoveryService | None = None
    if settings.deployment_mode is DeploymentMode.HOSTED:
        key_id = settings.credential_encryption_key_id
        if key_id is None:
            raise ValueError("Hosted worker credential encryption is not configured.")
        executor = SecureFirecrawlExecutor(settings)
        connection_service = FirecrawlConnectionService(
            PostgresFirecrawlConnectionRepository(engine),
            CredentialVault(
                FileKeyProvider.from_bytes(
                    key_id=key_id,
                    key=settings.credential_encryption_key(),
                )
            ),
            executor,
            remote_allowed_ports=settings.firecrawl_remote_allowed_ports,
        )
        scoped_discovery = HostedScopedDiscovery(connection_service, executor)
    elif settings.firecrawl_base_url is not None:
        normalized_base_url = str(settings.firecrawl_base_url).rstrip("/")
        firecrawl_http_client = httpx.AsyncClient(
            base_url=normalized_base_url,
            timeout=httpx.Timeout(settings.firecrawl_search_timeout_seconds),
        )
        scoped_discovery = SelfHostedScopedDiscovery(
            DiscoveryService(FirecrawlClient(firecrawl_http_client)),
            normalized_base_url,
        )
    discovery_workers = (
        tuple(
            DiscoveryJobWorker(
                repository=discovery_job_repository,
                discovery=scoped_discovery,
                fingerprint_secret=settings.discovery_fingerprint_secret(),
                worker_id=f"{settings.worker_id}-discovery-{slot}",
                lease_seconds=settings.discovery_lease_seconds,
                clock=lambda: datetime.now(UTC),
            )
            for slot in range(1, settings.discovery_worker_slots + 1)
        )
        if scoped_discovery is not None
        else ()
    )
    policy = NetworkPolicy(
        allowed_public_ports=settings.download_allowed_public_ports,
        private_allowlist=settings.download_private_allowlist,
    )

    def downloader() -> BoundedDownloader:
        return BoundedDownloader(
            policy=policy,
            max_bytes=settings.download_max_bytes,
            connect_timeout=settings.download_connect_timeout_seconds,
            read_timeout=settings.download_read_timeout_seconds,
            total_timeout=settings.download_total_timeout_seconds,
            max_redirects=settings.download_max_redirects,
        )

    storage_runtime = create_storage_runtime(settings)
    artifact_store = storage_runtime.artifact_store
    scratch_storage = storage_runtime.scratch_storage
    export_storage = storage_runtime.export_storage
    validator = DocumentValidator(
        docx_max_expanded_bytes=settings.docx_max_expanded_bytes,
        docx_max_expansion_ratio=settings.docx_max_expansion_ratio,
    )
    acquisition_worker = AcquisitionWorker(
        repository=acquisition_repository,
        artifact_repository=artifact_repository,
        artifact_store=artifact_store,
        scratch_storage=scratch_storage,
        export_storage=export_storage,
        downloader=downloader(),
        validator=validator,
        worker_id=settings.worker_id,
        lease_seconds=settings.download_lease_seconds,
        max_attempts=settings.download_max_attempts,
        retry_base_seconds=settings.download_retry_base_seconds,
        max_staging_bytes=settings.download_max_bytes,
    )
    conversion_temporary_root = settings.scratch_root / ".analysis-conversion"
    await anyio.to_thread.run_sync(
        conversion_temporary_root.mkdir,
        0o700,
        True,
        True,
    )
    analysis_worker = AnalysisWorker(
        repository=analysis_repository,
        artifact_repository=artifact_repository,
        artifact_store=artifact_store,
        scratch_storage=scratch_storage,
        downloader=downloader(),
        validator=validator,
        parser=LiteParseAdapter(
            page_limit=settings.analysis_page_limit,
            parser_timeout_seconds=settings.analysis_parser_timeout_seconds,
            screenshot_dpi=150,
        ),
        converter=LibreOfficeDocxConverter(
            temporary_root=conversion_temporary_root,
            timeout_seconds=settings.analysis_conversion_timeout_seconds,
        ),
        worker_id=settings.worker_id,
        lease_seconds=settings.analysis_lease_seconds,
        max_attempts=settings.download_max_attempts,
        retry_base_seconds=settings.download_retry_base_seconds,
        max_artifact_bytes=max(
            settings.download_max_bytes,
            settings.docx_max_expanded_bytes,
        ),
    )
    maintenance_worker = ArtifactMaintenanceService(
        repository=artifact_repository,
        artifact_store=artifact_store,
        scratch_storage=scratch_storage,
        worker_id=settings.worker_id,
        orphan_grace_seconds=settings.storage_orphan_grace_seconds,
        scratch_stale_seconds=settings.scratch_stale_seconds,
        lease_seconds=settings.download_lease_seconds,
        retry_base_seconds=settings.download_retry_base_seconds,
    )
    stop = anyio.Event()

    async def heartbeat() -> None:
        await write_heartbeat(engine, settings.worker_id)

    try:
        await serve_worker(
            analysis_worker=analysis_worker,
            acquisition_worker=acquisition_worker,
            heartbeat=heartbeat,
            heartbeat_seconds=settings.worker_heartbeat_seconds,
            maintenance_worker=maintenance_worker,
            maintenance_seconds=30.0,
            idle_seconds=1.0,
            stop=stop,
            discovery_workers=discovery_workers,
        )
    finally:
        await analysis_worker.aclose()
        await acquisition_worker.aclose()
        if firecrawl_http_client is not None:
            await firecrawl_http_client.aclose()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(run())
