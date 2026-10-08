from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

from parserium_collector.features.acquisition.export_storage import LocalExportStorage
from parserium_collector.features.storage.errors import ArtifactConfigurationError
from parserium_collector.features.storage.filesystem import FilesystemArtifactStore
from parserium_collector.features.storage.protocols import ArtifactStore
from parserium_collector.features.storage.s3 import S3ArtifactStore
from parserium_collector.features.storage.scratch import ScratchStorage
from parserium_collector.settings import DeploymentMode, Settings, StorageBackend


@dataclass(frozen=True)
class StorageRuntime:
    artifact_store: ArtifactStore
    scratch_storage: ScratchStorage
    export_storage: LocalExportStorage | None


def create_storage_runtime(settings: Settings) -> StorageRuntime:
    export_storage = None
    if settings.deployment_mode is DeploymentMode.SELF_HOSTED and settings.export_root.is_dir():
        export_storage = LocalExportStorage(export_root=settings.export_root)
    return StorageRuntime(
        artifact_store=create_artifact_store(settings),
        scratch_storage=ScratchStorage(
            settings.scratch_root,
            reserve_bytes=settings.storage_reserve_bytes,
            reserve_ratio=settings.storage_reserve_ratio,
        ),
        export_storage=export_storage,
    )


def create_artifact_store(settings: Settings) -> ArtifactStore:
    if settings.storage_backend is StorageBackend.FILESYSTEM:
        return FilesystemArtifactStore(
            settings.storage_root,
            reserve_bytes=settings.storage_reserve_bytes,
            reserve_ratio=settings.storage_reserve_ratio,
        )
    if not settings.storage_region or not settings.storage_bucket:
        raise ArtifactConfigurationError("S3 durable storage configuration is incomplete.")

    client_options = _s3_client_options(settings)
    try:
        client: S3Client = boto3.client("s3", **client_options)
        signing_client: S3Client | None = None
        if settings.storage_signed_url_endpoint is not None:
            signing_options = dict(client_options)
            signing_options["endpoint_url"] = str(settings.storage_signed_url_endpoint).rstrip("/")
            signing_client = boto3.client("s3", **signing_options)
    except (BotoCoreError, ValueError) as error:
        raise ArtifactConfigurationError("S3 client configuration failed.") from error
    return S3ArtifactStore(
        client=client,
        signing_client=signing_client,
        bucket=settings.storage_bucket,
        max_concurrency=settings.storage_max_concurrency,
    )


def _s3_client_options(settings: Settings) -> dict[str, Any]:
    client_options: dict[str, Any] = {
        "region_name": settings.storage_region,
        "config": Config(
            signature_version="s3v4",
            connect_timeout=10,
            read_timeout=60,
            retries={"total_max_attempts": 3, "mode": "standard"},
            s3={"addressing_style": "path" if settings.storage_force_path_style else "virtual"},
        ),
        "verify": (
            str(settings.storage_ca_bundle_file)
            if settings.storage_ca_bundle_file is not None
            else True
        ),
    }
    if settings.storage_endpoint is not None:
        client_options["endpoint_url"] = str(settings.storage_endpoint).rstrip("/")
    if settings.storage_access_key_file is not None:
        if settings.storage_secret_key_file is None:
            raise ArtifactConfigurationError("S3 credential configuration is incomplete.")
        client_options["aws_access_key_id"] = _read_secret(
            settings.storage_access_key_file,
            "access credential",
        )
        client_options["aws_secret_access_key"] = _read_secret(
            settings.storage_secret_key_file,
            "secret credential",
        )
        if settings.storage_session_token_file is not None:
            client_options["aws_session_token"] = _read_secret(
                settings.storage_session_token_file,
                "session credential",
            )
    return client_options


def _read_secret(path: Path, label: str) -> str:
    try:
        value = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ArtifactConfigurationError(f"The S3 {label} file is unreadable.") from error
    value = value.removesuffix("\n").removesuffix("\r")
    if not value:
        raise ArtifactConfigurationError(f"The S3 {label} file contains no credential.")
    return value
