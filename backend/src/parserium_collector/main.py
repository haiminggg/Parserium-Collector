import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import httpx
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from parserium_collector.adapters.database.engine import create_engine
from parserium_collector.adapters.firecrawl.client import FirecrawlClient
from parserium_collector.adapters.firecrawl.health import firecrawl_health_probe
from parserium_collector.features.acquisition.repository import (
    PostgresAcquisitionRepository,
)
from parserium_collector.features.acquisition.router import router as acquisition_router
from parserium_collector.features.acquisition.service import AcquisitionService
from parserium_collector.features.acquisition.validation import DocumentValidator
from parserium_collector.features.activity.repository import PostgresActivityRepository
from parserium_collector.features.activity.router import router as activity_router
from parserium_collector.features.activity.service import ActivityService
from parserium_collector.features.analysis.job_repository import (
    PostgresDiscoveryJobRepository,
)
from parserium_collector.features.analysis.repository import (
    PostgresAnalysisRepository,
)
from parserium_collector.features.analysis.router import router as analysis_router
from parserium_collector.features.analysis.service import AnalysisService
from parserium_collector.features.discovery.router import router as discovery_router
from parserium_collector.features.discovery.scoped import (
    HostedScopedDiscovery,
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
from parserium_collector.features.firecrawl_connections.router import (
    router as firecrawl_connections_router,
)
from parserium_collector.features.firecrawl_connections.service import (
    FirecrawlConnectionService,
)
from parserium_collector.features.health.probes import database_probe, storage_probe, worker_probe
from parserium_collector.features.health.router import router as health_router
from parserium_collector.features.health.service import (
    HealthService,
    Probe,
    unavailable_health_service,
)
from parserium_collector.features.identity.oidc import OidcAdapter
from parserium_collector.features.identity.repository import PostgresIdentityRepository
from parserium_collector.features.identity.router import router as identity_router
from parserium_collector.features.identity.service import IdentityService
from parserium_collector.features.session.crypto import keyed_digest
from parserium_collector.features.session.repository import PostgresSessionRepository
from parserium_collector.features.session.router import router as session_router
from parserium_collector.features.session.service import SessionService
from parserium_collector.features.storage.access import ArtifactAccessService
from parserium_collector.features.storage.factory import create_storage_runtime
from parserium_collector.features.storage.repository import PostgresArtifactRepository
from parserium_collector.security import LocalRequestGuardMiddleware
from parserium_collector.settings import DeploymentMode, Settings, StorageBackend

LOGGER = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    health_service: HealthService | None = None,
    session_service: SessionService | None = None,
    identity_service: IdentityService | None = None,
    oidc_adapter: OidcAdapter | None = None,
    discovery_service: DiscoveryService | None = None,
    acquisition_service: AcquisitionService | None = None,
    analysis_service: AnalysisService | None = None,
    firecrawl_connection_service: FirecrawlConnectionService | None = None,
    activity_service: ActivityService | None = None,
) -> FastAPI:
    resolved = settings or Settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        engine = None
        firecrawl_http_client = None
        artifact_repository = None
        artifact_store = None
        storage_runtime = None
        if health_service is not None:
            application.state.health_service = health_service
        else:
            try:
                engine = create_engine(resolved)
                artifact_repository = PostgresArtifactRepository(engine)
                storage_runtime = create_storage_runtime(resolved)
                artifact_store = storage_runtime.artifact_store
                probes: tuple[Probe, ...] = (
                    database_probe(engine, resolved.expected_migration),
                    storage_probe(
                        artifact_store,
                        resolved.worker_id,
                        legacy_repository=artifact_repository,
                        reject_legacy_pending=(resolved.storage_backend is StorageBackend.S3),
                    ),
                    worker_probe(engine, resolved.worker_stale_seconds),
                )
                if resolved.deployment_mode is DeploymentMode.SELF_HOSTED:
                    probes += (
                        firecrawl_health_probe(
                            str(resolved.firecrawl_base_url).rstrip("/")
                            if resolved.firecrawl_base_url is not None
                            else None,
                            resolved.firecrawl_timeout_seconds,
                        ),
                    )
                application.state.health_service = HealthService(resolved.build_id, probes)
            except (OSError, ValueError):
                application.state.health_service = unavailable_health_service(
                    resolved.build_id,
                    "Required installation settings are unavailable.",
                )
        if resolved.deployment_mode is DeploymentMode.HOSTED:
            key_id = resolved.credential_encryption_key_id
            if key_id is None:
                raise ValueError(
                    "Hosted API mode requires a credential encryption key file and identifier."
                )
            wrapping_key = resolved.credential_encryption_key()
            application.state.oidc_adapter = oidc_adapter or OidcAdapter(resolved)
            if firecrawl_connection_service is not None:
                application.state.firecrawl_connection_service = firecrawl_connection_service
            elif engine is not None:
                connection_repository = PostgresFirecrawlConnectionRepository(engine)
                credential_vault = CredentialVault(
                    FileKeyProvider.from_bytes(key_id=key_id, key=wrapping_key)
                )
                firecrawl_executor = SecureFirecrawlExecutor(resolved)
                connection_service = FirecrawlConnectionService(
                    connection_repository,
                    credential_vault,
                    firecrawl_executor,
                    remote_allowed_ports=resolved.firecrawl_remote_allowed_ports,
                )
                application.state.firecrawl_connection_repository = connection_repository
                application.state.firecrawl_credential_vault = credential_vault
                application.state.firecrawl_executor = firecrawl_executor
                application.state.firecrawl_connection_service = connection_service
                application.state.discovery_service = HostedScopedDiscovery(
                    connection_service,
                    firecrawl_executor,
                )
            else:
                application.state.firecrawl_connection_service = None
            if identity_service is not None:
                application.state.identity_service = identity_service
            elif engine is not None:
                application.state.identity_service = IdentityService(
                    repository=PostgresIdentityRepository(engine),
                    signing_secret=resolved.session_signing_secret(),
                    session_idle_ttl=timedelta(seconds=resolved.session_idle_seconds),
                )
        else:
            if session_service is not None:
                application.state.session_service = session_service
            elif health_service is None and engine is not None:
                application.state.session_service = SessionService(
                    repository=PostgresSessionRepository(engine),
                    signing_secret=resolved.session_signing_secret(),
                    pairing_ttl=timedelta(seconds=resolved.pairing_ttl_seconds),
                    session_idle_ttl=timedelta(seconds=resolved.session_idle_seconds),
                )
        if resolved.deployment_mode is DeploymentMode.SELF_HOSTED and hasattr(
            application.state, "session_service"
        ):
            pairing = await application.state.session_service.ensure_pairing_code(datetime.now(UTC))
            if pairing is not None:
                LOGGER.warning(
                    "LOCAL PAIRING CODE: %s (expires at %s)",
                    pairing.code,
                    pairing.expires_at.isoformat(),
                )
        if resolved.deployment_mode is DeploymentMode.HOSTED:
            if not hasattr(application.state, "discovery_service"):
                application.state.discovery_service = None
        elif discovery_service is not None:
            application.state.discovery_service = SelfHostedScopedDiscovery(
                discovery_service,
                (
                    str(resolved.firecrawl_base_url).rstrip("/")
                    if resolved.firecrawl_base_url is not None
                    else None
                ),
            )
        elif resolved.firecrawl_base_url is not None:
            firecrawl_http_client = httpx.AsyncClient(
                base_url=str(resolved.firecrawl_base_url).rstrip("/"),
                timeout=httpx.Timeout(resolved.firecrawl_search_timeout_seconds),
            )
            application.state.discovery_service = SelfHostedScopedDiscovery(
                DiscoveryService(FirecrawlClient(firecrawl_http_client)),
                str(resolved.firecrawl_base_url).rstrip("/"),
            )
        else:
            application.state.discovery_service = None
        if engine is not None and artifact_repository is None:
            artifact_repository = PostgresArtifactRepository(engine)
        if engine is not None and storage_runtime is None:
            storage_runtime = create_storage_runtime(resolved)
            artifact_store = storage_runtime.artifact_store
        artifact_access = (
            ArtifactAccessService(
                artifact_repository,
                artifact_store,
                signed_url_ttl_seconds=resolved.storage_signed_url_ttl_seconds,
            )
            if artifact_repository is not None and artifact_store is not None
            else None
        )
        scratch_storage = storage_runtime.scratch_storage if storage_runtime is not None else None
        if acquisition_service is not None:
            application.state.acquisition_service = acquisition_service
            application.state.document_storage = None
        elif engine is not None:
            if artifact_repository is None or artifact_access is None:
                raise RuntimeError("Artifact services are unavailable.")
            application.state.document_storage = None
            application.state.acquisition_service = AcquisitionService(
                PostgresAcquisitionRepository(engine, artifact_repository),
                export_available=(
                    storage_runtime is not None and storage_runtime.export_storage is not None
                ),
                artifact_access=artifact_access,
            )
        else:
            application.state.acquisition_service = None
            application.state.document_storage = None
        if analysis_service is not None:
            application.state.analysis_service = analysis_service
        elif (
            engine is not None and getattr(application.state, "discovery_service", None) is not None
        ):
            if (
                artifact_repository is None
                or artifact_store is None
                or scratch_storage is None
                or artifact_access is None
            ):
                raise RuntimeError("Artifact services are unavailable.")
            application.state.analysis_service = AnalysisService(
                repository=PostgresAnalysisRepository(engine, artifact_repository),
                discovery_service=application.state.discovery_service,
                session_ttl=timedelta(seconds=resolved.analysis_session_ttl_seconds),
                session_byte_limit=resolved.analysis_session_max_bytes,
                job_repository=PostgresDiscoveryJobRepository(engine),
                fingerprint_secret=resolved.discovery_fingerprint_secret(),
                validator=DocumentValidator(
                    docx_max_expanded_bytes=resolved.docx_max_expanded_bytes,
                    docx_max_expansion_ratio=resolved.docx_max_expansion_ratio,
                ),
                artifact_repository=artifact_repository,
                artifact_store=artifact_store,
                scratch_storage=scratch_storage,
                artifact_access=artifact_access,
            )
        else:
            application.state.analysis_service = None
        if activity_service is not None:
            application.state.activity_service = activity_service
        elif engine is not None:
            application.state.activity_service = ActivityService(
                PostgresActivityRepository(engine)
            )
        else:
            application.state.activity_service = None
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
    if resolved.deployment_mode is DeploymentMode.HOSTED:
        app.add_middleware(
            SessionMiddleware,
            secret_key=keyed_digest(
                resolved.session_signing_secret(),
                "oidc-flow-cookie",
                "v1",
            ),
            session_cookie="parserium_oidc_flow",
            max_age=resolved.oidc_flow_ttl_seconds,
            same_site="lax",
            https_only=True,
        )
    app.include_router(health_router)
    app.include_router(identity_router)
    app.include_router(session_router)
    app.include_router(discovery_router)
    app.include_router(analysis_router)
    app.include_router(acquisition_router)
    app.include_router(activity_router)
    app.include_router(firecrawl_connections_router)
    if resolved.static_root.is_dir():
        app.mount("/", StaticFiles(directory=resolved.static_root, html=True), name="web")
    return app


app = create_app()
