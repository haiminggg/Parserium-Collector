import base64
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient

import parserium_collector.main as main_module
from parserium_collector.features.discovery.scoped import (
    HostedScopedDiscovery,
    SelfHostedScopedDiscovery,
)
from parserium_collector.features.firecrawl_connections.crypto import CredentialVault
from parserium_collector.features.firecrawl_connections.executor import SecureFirecrawlExecutor
from parserium_collector.features.firecrawl_connections.repository import (
    PostgresFirecrawlConnectionRepository,
)
from parserium_collector.features.firecrawl_connections.service import (
    FirecrawlConnectionService,
)
from parserium_collector.features.health.models import ComponentHealth, ComponentStatus
from parserium_collector.features.health.service import unavailable_health_service
from parserium_collector.features.identity.oidc import OidcAdapter
from parserium_collector.features.identity.service import IdentityService
from parserium_collector.features.session.models import IssuedPairingCode
from parserium_collector.features.session.service import SessionService
from parserium_collector.features.storage.factory import StorageRuntime
from parserium_collector.main import create_app
from parserium_collector.settings import DeploymentMode, Settings


def test_app_wires_session_routes_guards_and_one_startup_pairing_code(caplog: object) -> None:
    session_service = AsyncMock(spec=SessionService)
    session_service.ensure_pairing_code.return_value = IssuedPairingCode(
        code="local-pairing-code",
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )
    app = create_app(
        Settings(static_root="/missing"),
        health_service=unavailable_health_service("test", "test"),
        session_service=session_service,
    )

    with caplog.at_level(logging.WARNING):  # type: ignore[attr-defined]
        with TestClient(app, base_url="http://127.0.0.1:8080") as client:
            status = client.get("/api/v1/session/status")
            hostile = client.get(
                "/api/v1/health/live",
                headers={"Host": "attacker.invalid"},
            )

    assert status.status_code == 200
    assert status.json() == {"status": "pairing_required"}
    assert hostile.status_code == 400
    assert "/api/v1/discovery/search" in app.openapi()["paths"]
    assert "/api/v1/activity" in app.openapi()["paths"]
    session_service.ensure_pairing_code.assert_awaited_once()
    assert "local-pairing-code" in caplog.text  # type: ignore[attr-defined]


def test_app_wires_hosted_identity_without_local_pairing(tmp_path: Path) -> None:
    session_secret = tmp_path / "session-secret"
    session_secret.write_bytes(b"s" * 32)
    oidc_secret = tmp_path / "oidc-secret"
    oidc_secret.write_bytes(b"o" * 32)
    wrapping_key = tmp_path / "firecrawl-wrapping-key"
    wrapping_key.write_bytes(base64.b64encode(b"k" * 32))
    settings = Settings(
        deployment_mode=DeploymentMode.HOSTED,
        storage_backend="s3",
        storage_endpoint="https://minio.parserium.test",
        storage_region="us-east-1",
        storage_bucket="parserium-artifacts",
        public_origin="https://app.parserium.test",
        allowed_hosts=("app.parserium.test",),
        allowed_origins=("https://app.parserium.test",),
        session_cookie_secure=True,
        session_signing_secret_file=session_secret,
        oidc_issuer="https://identity.parserium.test",
        oidc_client_id="parserium-client",
        oidc_client_secret_file=oidc_secret,
        oidc_redirect_uri="https://app.parserium.test/api/v1/auth/callback",
        credential_encryption_key_file=wrapping_key,
        credential_encryption_key_id="hosted-v1",
        static_root=tmp_path / "missing",
    )
    local_sessions = AsyncMock(spec=SessionService)
    identity = AsyncMock(spec=IdentityService)
    oidc = AsyncMock(spec=OidcAdapter)
    connections = AsyncMock(spec=FirecrawlConnectionService)
    app = create_app(
        settings,
        health_service=unavailable_health_service("test", "test"),
        session_service=local_sessions,
        identity_service=identity,
        oidc_adapter=oidc,
        firecrawl_connection_service=connections,
    )

    with TestClient(app, base_url="https://app.parserium.test") as client:
        status = client.get("/api/v1/session/status")
        paired = client.post(
            "/api/v1/session/pair",
            json={"code": "unused"},
            headers={"Origin": "https://app.parserium.test"},
        )

    assert status.json() == {
        "status": "login_required",
        "login_url": "/api/v1/auth/login",
        "provider_label": "Identity provider",
    }
    assert paired.status_code == 404
    local_sessions.ensure_pairing_code.assert_not_awaited()
    assert "/api/v1/auth/login" in app.openapi()["paths"]
    assert "/api/v1/firecrawl/connections" in app.openapi()["paths"]
    assert app.state.firecrawl_connection_service is connections


def test_hosted_api_startup_requires_credential_encryption_key(tmp_path: Path) -> None:
    session_secret = tmp_path / "session-secret"
    session_secret.write_bytes(b"s" * 32)
    oidc_secret = tmp_path / "oidc-secret"
    oidc_secret.write_bytes(b"o" * 32)
    settings = Settings(
        deployment_mode=DeploymentMode.HOSTED,
        storage_backend="s3",
        storage_endpoint="https://minio.parserium.test",
        storage_region="us-east-1",
        storage_bucket="parserium-artifacts",
        public_origin="https://app.parserium.test",
        allowed_hosts=("app.parserium.test",),
        allowed_origins=("https://app.parserium.test",),
        session_cookie_secure=True,
        session_signing_secret_file=session_secret,
        oidc_issuer="https://identity.parserium.test",
        oidc_client_id="parserium-client",
        oidc_client_secret_file=oidc_secret,
        oidc_redirect_uri="https://app.parserium.test/api/v1/auth/callback",
        static_root=tmp_path / "missing",
    )
    app = create_app(
        settings,
        health_service=unavailable_health_service("test", "test"),
        identity_service=AsyncMock(spec=IdentityService),
        oidc_adapter=AsyncMock(spec=OidcAdapter),
    )

    with pytest.raises(ValueError, match="credential encryption key"):
        with TestClient(app, base_url="https://app.parserium.test"):
            pass


def test_hosted_runtime_constructs_workspace_connection_stack_without_global_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_secret = tmp_path / "session-secret"
    session_secret.write_bytes(b"s" * 32)
    oidc_secret = tmp_path / "oidc-secret"
    oidc_secret.write_bytes(b"o" * 32)
    wrapping_key = tmp_path / "firecrawl-wrapping-key"
    wrapping_key.write_bytes(base64.b64encode(b"k" * 32))
    fingerprint_secret = tmp_path / "fingerprint-secret"
    fingerprint_secret.write_bytes(b"f" * 32)
    settings = Settings(
        deployment_mode=DeploymentMode.HOSTED,
        storage_backend="s3",
        storage_endpoint="https://minio.parserium.test",
        storage_region="us-east-1",
        storage_bucket="parserium-artifacts",
        public_origin="https://app.parserium.test",
        allowed_hosts=("app.parserium.test",),
        allowed_origins=("https://app.parserium.test",),
        session_cookie_secure=True,
        session_signing_secret_file=session_secret,
        oidc_issuer="https://identity.parserium.test",
        oidc_client_id="parserium-client",
        oidc_client_secret_file=oidc_secret,
        oidc_redirect_uri="https://app.parserium.test/api/v1/auth/callback",
        credential_encryption_key_file=wrapping_key,
        credential_encryption_key_id="hosted-v1",
        discovery_fingerprint_secret_file=fingerprint_secret,
        static_root=tmp_path / "missing",
    )
    engine = AsyncMock()
    artifact_store = Mock(name="artifact-store")
    runtime = StorageRuntime(
        artifact_store=artifact_store,
        scratch_storage=Mock(name="scratch-storage"),
        export_storage=None,
    )
    firecrawl_probe_calls: list[tuple[object, object]] = []

    async def available_probe() -> ComponentHealth:
        return ComponentHealth(name="test", status=ComponentStatus.AVAILABLE)

    monkeypatch.setattr(main_module, "create_engine", lambda actual: engine)
    monkeypatch.setattr(main_module, "create_storage_runtime", lambda actual: runtime)
    monkeypatch.setattr(main_module, "database_probe", lambda *args: available_probe)
    monkeypatch.setattr(main_module, "storage_probe", lambda *args, **kwargs: available_probe)
    monkeypatch.setattr(main_module, "worker_probe", lambda *args: available_probe)
    monkeypatch.setattr(
        main_module,
        "firecrawl_health_probe",
        lambda base_url, timeout: firecrawl_probe_calls.append((base_url, timeout)),
    )
    app = create_app(
        settings,
        identity_service=AsyncMock(spec=IdentityService),
        oidc_adapter=AsyncMock(spec=OidcAdapter),
    )

    with TestClient(app, base_url="https://app.parserium.test"):
        assert isinstance(
            app.state.firecrawl_connection_repository,
            PostgresFirecrawlConnectionRepository,
        )
        assert isinstance(app.state.firecrawl_credential_vault, CredentialVault)
        assert isinstance(app.state.firecrawl_executor, SecureFirecrawlExecutor)
        assert isinstance(app.state.firecrawl_connection_service, FirecrawlConnectionService)
        assert isinstance(app.state.discovery_service, HostedScopedDiscovery)
        assert app.state.analysis_service.discovery_service is app.state.discovery_service

    assert firecrawl_probe_calls == []
    engine.dispose.assert_awaited_once()


def test_app_constructs_one_storage_runtime_shared_by_all_api_consumers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    export_root = tmp_path / "exports"
    export_root.mkdir()
    fingerprint_secret = tmp_path / "fingerprint-secret"
    fingerprint_secret.write_bytes(b"f" * 32)
    settings = Settings(
        discovery_fingerprint_secret_file=fingerprint_secret,
        static_root=tmp_path / "missing",
        storage_root=tmp_path / "durable",
        scratch_root=tmp_path / "scratch",
        export_root=export_root,
    )
    engine = AsyncMock()
    artifact_store = Mock(name="artifact-store")
    scratch_storage = Mock(name="scratch-storage")
    export_storage = Mock(name="export-storage")
    runtime = StorageRuntime(
        artifact_store=artifact_store,
        scratch_storage=scratch_storage,
        export_storage=export_storage,
    )
    artifact_repository = Mock(name="artifact-repository")
    runtime_calls: list[Settings] = []
    probe_stores: list[object] = []
    firecrawl_probe_calls: list[tuple[str | None, float]] = []

    def runtime_factory(actual: Settings) -> StorageRuntime:
        runtime_calls.append(actual)
        return runtime

    def durable_probe(store: object, *args: object, **kwargs: object):
        probe_stores.append(store)

        async def probe() -> ComponentHealth:
            return ComponentHealth(name="storage", status=ComponentStatus.AVAILABLE)

        return probe

    monkeypatch.setattr(main_module, "create_engine", lambda actual: engine)
    monkeypatch.setattr(main_module, "create_storage_runtime", runtime_factory)
    monkeypatch.setattr(
        main_module,
        "PostgresArtifactRepository",
        lambda actual: artifact_repository,
    )
    monkeypatch.setattr(main_module, "storage_probe", durable_probe)

    def optional_firecrawl_probe(base_url: str | None, timeout: float):
        firecrawl_probe_calls.append((base_url, timeout))

        async def probe() -> ComponentHealth:
            return ComponentHealth(name="firecrawl", status=ComponentStatus.NOT_CONFIGURED)

        return probe

    monkeypatch.setattr(main_module, "firecrawl_health_probe", optional_firecrawl_probe)
    session_service = AsyncMock(spec=SessionService)
    session_service.ensure_pairing_code.return_value = None
    discovery_service = AsyncMock()
    app = create_app(
        settings,
        session_service=session_service,
        discovery_service=discovery_service,
    )

    with TestClient(app, base_url="http://127.0.0.1:8080"):
        analysis = app.state.analysis_service
        acquisition = app.state.acquisition_service

        assert analysis.artifact_store is artifact_store
        assert analysis.scratch_storage is scratch_storage
        assert analysis.artifact_repository is artifact_repository
        assert analysis.artifact_access._store is artifact_store
        assert acquisition._artifact_access._store is artifact_store
        assert acquisition._artifact_access._repository is artifact_repository
        assert isinstance(app.state.discovery_service, SelfHostedScopedDiscovery)
        assert not hasattr(app.state, "firecrawl_connection_service")

    assert runtime_calls == [settings]
    assert probe_stores == [artifact_store]
    assert firecrawl_probe_calls == [(None, settings.firecrawl_timeout_seconds)]
    engine.dispose.assert_awaited_once()
