from pathlib import Path
from secrets import token_urlsafe
from typing import Any

import boto3
import pytest
from botocore.config import Config

from parserium_collector.features.acquisition.export_storage import LocalExportStorage
from parserium_collector.features.storage.errors import ArtifactConfigurationError
from parserium_collector.features.storage.factory import (
    create_artifact_store,
    create_storage_runtime,
)
from parserium_collector.features.storage.filesystem import FilesystemArtifactStore
from parserium_collector.features.storage.s3 import S3ArtifactStore
from parserium_collector.features.storage.scratch import ScratchStorage
from parserium_collector.settings import Settings


class CapturedClient:
    """Marker test double returned by the patched Boto3 client factory."""


def hosted_storage_settings(tmp_path: Path, **overrides: object) -> Settings:
    oidc_secret = tmp_path / "oidc-secret"
    oidc_secret.write_text(token_urlsafe(48), encoding="utf-8")
    values: dict[str, object] = {
        "deployment_mode": "hosted",
        "public_origin": "https://app.parserium.test",
        "allowed_hosts": ("app.parserium.test",),
        "allowed_origins": ("https://app.parserium.test",),
        "session_cookie_secure": True,
        "oidc_issuer": "https://identity.parserium.test",
        "oidc_client_id": "parserium-web",
        "oidc_client_secret_file": oidc_secret,
        "oidc_redirect_uri": "https://app.parserium.test/api/v1/auth/callback",
        "storage_backend": "s3",
        "storage_endpoint": "https://minio.parserium.test",
        "storage_region": "us-east-1",
        "storage_bucket": "parserium-artifacts",
        "storage_force_path_style": True,
        "storage_max_concurrency": 3,
    }
    values.update(overrides)
    return Settings(**values)


def capture_boto_client(monkeypatch: pytest.MonkeyPatch) -> tuple[CapturedClient, dict[str, Any]]:
    captured: dict[str, Any] = {}
    client = CapturedClient()

    def fake_client(service_name: str, **kwargs: Any) -> CapturedClient:
        captured["service_name"] = service_name
        captured.update(kwargs)
        return client

    monkeypatch.setattr(boto3, "client", fake_client)
    return client, captured


def test_factory_builds_the_filesystem_backend(tmp_path: Path) -> None:
    settings = Settings(storage_root=tmp_path / "durable")

    assert isinstance(create_artifact_store(settings), FilesystemArtifactStore)


def test_runtime_factory_builds_self_hosted_durable_scratch_and_export_storage(
    tmp_path: Path,
) -> None:
    export_root = tmp_path / "exports"
    export_root.mkdir()
    settings = Settings(
        storage_root=tmp_path / "durable",
        scratch_root=tmp_path / "scratch",
        export_root=export_root,
    )

    runtime = create_storage_runtime(settings)

    assert isinstance(runtime.artifact_store, FilesystemArtifactStore)
    assert isinstance(runtime.scratch_storage, ScratchStorage)
    assert isinstance(runtime.export_storage, LocalExportStorage)


def test_runtime_factory_builds_hosted_s3_and_scratch_without_local_export(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture_boto_client(monkeypatch)

    runtime = create_storage_runtime(
        hosted_storage_settings(
            tmp_path,
            scratch_root=tmp_path / "scratch",
            export_root=tmp_path / "exports",
        )
    )

    assert isinstance(runtime.artifact_store, S3ArtifactStore)
    assert isinstance(runtime.scratch_storage, ScratchStorage)
    assert runtime.export_storage is None


def test_factory_builds_the_expected_s3_client_without_static_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, captured = capture_boto_client(monkeypatch)

    store = create_artifact_store(hosted_storage_settings(tmp_path))

    assert isinstance(store, S3ArtifactStore)
    assert store.client is client
    assert captured["service_name"] == "s3"
    assert captured["endpoint_url"] == "https://minio.parserium.test"
    assert captured["region_name"] == "us-east-1"
    assert captured["verify"] is True
    assert "aws_access_key_id" not in captured
    assert "aws_secret_access_key" not in captured
    config = captured["config"]
    assert isinstance(config, Config)
    assert config.signature_version == "s3v4"
    assert config.connect_timeout == 10
    assert config.read_timeout == 60
    assert config.retries == {"total_max_attempts": 3, "mode": "standard"}
    assert config.s3 == {"addressing_style": "path"}


def test_factory_reuses_one_client_for_operations_and_signing_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[CapturedClient] = []

    def fake_client(service_name: str, **kwargs: Any) -> CapturedClient:
        assert service_name == "s3"
        client = CapturedClient()
        calls.append(client)
        return client

    monkeypatch.setattr(boto3, "client", fake_client)

    store = create_artifact_store(hosted_storage_settings(tmp_path))

    assert isinstance(store, S3ArtifactStore)
    assert len(calls) == 1
    assert store.client is calls[0]
    assert store.signing_client is calls[0]


def test_factory_uses_a_separate_client_only_for_browser_signing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    clients = [CapturedClient(), CapturedClient()]

    def fake_client(service_name: str, **kwargs: Any) -> CapturedClient:
        calls.append({"service_name": service_name, **kwargs})
        return clients[len(calls) - 1]

    monkeypatch.setattr(boto3, "client", fake_client)

    store = create_artifact_store(
        hosted_storage_settings(
            tmp_path,
            storage_signed_url_endpoint="https://localhost:9000",
        )
    )

    assert isinstance(store, S3ArtifactStore)
    assert store.client is clients[0]
    assert store.signing_client is clients[1]
    assert calls[0]["endpoint_url"] == "https://minio.parserium.test"
    assert calls[1]["endpoint_url"] == "https://localhost:9000"
    assert "aws_access_key_id" not in calls[0]
    assert "aws_access_key_id" not in calls[1]


def test_factory_gives_both_s3_clients_the_same_credentials_and_transport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    access = tmp_path / "access"
    secret = tmp_path / "secret"
    token = tmp_path / "token"
    ca = tmp_path / "ca.pem"
    access.write_text("access-value\n", encoding="utf-8")
    secret.write_text("secret-value\n", encoding="utf-8")
    token.write_text("token-value\n", encoding="utf-8")
    ca.write_text("test CA", encoding="utf-8")
    calls: list[dict[str, Any]] = []

    def fake_client(service_name: str, **kwargs: Any) -> CapturedClient:
        calls.append({"service_name": service_name, **kwargs})
        return CapturedClient()

    monkeypatch.setattr(boto3, "client", fake_client)

    create_artifact_store(
        hosted_storage_settings(
            tmp_path,
            storage_signed_url_endpoint="https://localhost:9000",
            storage_access_key_file=access,
            storage_secret_key_file=secret,
            storage_session_token_file=token,
            storage_ca_bundle_file=ca,
        )
    )

    assert len(calls) == 2
    for name in (
        "region_name",
        "config",
        "verify",
        "aws_access_key_id",
        "aws_secret_access_key",
        "aws_session_token",
    ):
        assert calls[0][name] == calls[1][name]


def test_factory_reads_explicit_credentials_and_ca_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    access = tmp_path / "access"
    secret = tmp_path / "secret"
    token = tmp_path / "token"
    ca = tmp_path / "ca.pem"
    access.write_text("access-value\n", encoding="utf-8")
    secret.write_text("secret-value\n", encoding="utf-8")
    token.write_text("token-value\n", encoding="utf-8")
    ca.write_text("test CA", encoding="utf-8")
    _, captured = capture_boto_client(monkeypatch)

    create_artifact_store(
        hosted_storage_settings(
            tmp_path,
            storage_access_key_file=access,
            storage_secret_key_file=secret,
            storage_session_token_file=token,
            storage_ca_bundle_file=ca,
        )
    )

    assert captured["aws_access_key_id"] == "access-value"
    assert captured["aws_secret_access_key"] == "secret-value"  # noqa: S105
    assert captured["aws_session_token"] == "token-value"  # noqa: S105
    assert captured["verify"] == str(ca)


def test_factory_rejects_an_empty_explicit_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    access = tmp_path / "access"
    secret = tmp_path / "secret"
    access.write_text("access-value", encoding="utf-8")
    secret.write_text("\n", encoding="utf-8")
    capture_boto_client(monkeypatch)

    with pytest.raises(ArtifactConfigurationError, match="credential"):
        create_artifact_store(
            hosted_storage_settings(
                tmp_path,
                storage_access_key_file=access,
                storage_secret_key_file=secret,
            )
        )
