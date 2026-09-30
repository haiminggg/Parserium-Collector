import base64
import binascii
import os
import re
from enum import StrEnum
from pathlib import Path

from pydantic import AnyHttpUrl, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

from parserium_collector.features.acquisition.errors import AcquisitionError
from parserium_collector.features.acquisition.network_policy import NetworkPolicy


class DeploymentMode(StrEnum):
    SELF_HOSTED = "self_hosted"
    HOSTED = "hosted"


class StorageBackend(StrEnum):
    FILESYSTEM = "filesystem"
    S3 = "s3"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DASHBOARD_",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "Parserium Collector"
    build_id: str = "dev"
    release_version: str = "0.1.0-dev"
    deployment_mode: DeploymentMode = DeploymentMode.SELF_HOSTED
    public_origin: AnyHttpUrl = AnyHttpUrl("http://127.0.0.1:8080")
    allowed_hosts: tuple[str, ...] = ("127.0.0.1", "localhost")
    allowed_origins: tuple[str, ...] = (
        "http://127.0.0.1:8080",
        "http://localhost:8080",
    )
    storage_backend: StorageBackend = StorageBackend.FILESYSTEM
    storage_root: Path = Path("/var/lib/parserium-collector")
    storage_endpoint: AnyHttpUrl | None = None
    storage_signed_url_endpoint: AnyHttpUrl | None = None
    storage_region: str | None = None
    storage_bucket: str | None = None
    storage_force_path_style: bool = False
    storage_access_key_file: Path | None = None
    storage_secret_key_file: Path | None = None
    storage_session_token_file: Path | None = None
    storage_ca_bundle_file: Path | None = None
    storage_signed_url_ttl_seconds: int = Field(default=60, ge=30, le=300)
    storage_orphan_grace_seconds: int = Field(default=3600, ge=60, le=604800)
    storage_max_concurrency: int = Field(default=4, ge=1, le=32)
    scratch_root: Path = Path("/tmp/parserium-collector")  # noqa: S108
    scratch_stale_seconds: int = Field(default=86400, ge=300, le=604800)
    static_root: Path = Path("/app/static")
    storage_reserve_bytes: int = 5 * 1024 * 1024 * 1024
    storage_reserve_ratio: float = 0.10
    db_host: str = "db"
    db_port: int = 5432
    db_name: str = "parserium_collector"
    db_user: str = "parserium_collector"
    db_password_file: Path = Path("/run/secrets/db_password")
    session_signing_secret_file: Path = Path("/run/secrets/session_signing_secret")
    discovery_fingerprint_secret_file: Path = Path(
        "/run/secrets/discovery_fingerprint_secret"
    )
    pairing_ttl_seconds: int = Field(default=600, ge=60, le=3600)
    session_idle_seconds: int = Field(default=86400, ge=300, le=604800)
    session_cookie_secure: bool = False
    oidc_issuer: AnyHttpUrl | None = None
    oidc_client_id: str | None = None
    oidc_client_secret_file: Path | None = None
    oidc_ca_bundle_file: Path | None = None
    oidc_redirect_uri: AnyHttpUrl | None = None
    oidc_flow_ttl_seconds: int = Field(default=600, ge=60, le=900)
    oidc_provider_label: str = "Identity provider"
    credential_encryption_key_file: Path | None = None
    credential_encryption_key_id: str | None = None
    expected_migration: str = "0009_unified_activity_history"
    worker_id: str = "worker-1"
    worker_heartbeat_seconds: float = 10.0
    worker_stale_seconds: float = 30.0
    firecrawl_base_url: AnyHttpUrl | None = None
    firecrawl_timeout_seconds: float = 5.0
    firecrawl_search_timeout_seconds: float = Field(default=45.0, gt=0, le=120)
    firecrawl_remote_allowed_ports: tuple[int, ...] = (443,)
    firecrawl_remote_private_allowlist: tuple[str, ...] = ()
    firecrawl_validation_timeout_seconds: float = Field(default=15.0, gt=0, le=60)
    firecrawl_response_limit_bytes: int = Field(
        default=2 * 1024 * 1024,
        ge=1024,
        le=8 * 1024 * 1024,
    )
    download_max_bytes: int = Field(default=100 * 1024 * 1024, ge=1024 * 1024, le=2**31)
    download_connect_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    download_read_timeout_seconds: float = Field(default=60.0, gt=0, le=300)
    download_total_timeout_seconds: float = Field(default=600.0, gt=0, le=3600)
    download_max_redirects: int = Field(default=5, ge=0, le=10)
    download_max_attempts: int = Field(default=3, ge=1, le=10)
    download_retry_base_seconds: int = Field(default=30, ge=1, le=3600)
    download_lease_seconds: int = Field(default=60, ge=15, le=600)
    download_allowed_public_ports: tuple[int, ...] = (80, 443)
    download_private_allowlist: tuple[str, ...] = ()
    export_root: Path = Path("/exports")
    docx_max_expanded_bytes: int = Field(
        default=500 * 1024 * 1024,
        ge=1024 * 1024,
        le=4 * 1024 * 1024 * 1024,
    )
    docx_max_expansion_ratio: float = Field(default=100.0, gt=0, le=1000)
    analysis_session_max_bytes: int = Field(
        default=512 * 1024 * 1024,
        ge=1024 * 1024,
        le=4 * 1024 * 1024 * 1024,
    )
    analysis_page_limit: int = Field(default=200, ge=1, le=1000)
    analysis_session_ttl_seconds: int = Field(default=3600, ge=300, le=86400)
    analysis_parser_timeout_seconds: float = Field(default=120.0, gt=0, le=600)
    analysis_conversion_timeout_seconds: float = Field(default=120.0, gt=0, le=600)
    analysis_lease_seconds: int = Field(default=60, ge=15, le=600)
    discovery_lease_seconds: int = Field(default=60, ge=15, le=600)
    discovery_cache_seconds: int = Field(default=900, ge=60, le=3600)
    discovery_worker_slots: int = Field(default=5, ge=1, le=32)

    @field_validator("download_allowed_public_ports")
    @classmethod
    def validate_download_allowed_public_ports(
        cls,
        value: tuple[int, ...],
    ) -> tuple[int, ...]:
        if not value:
            raise ValueError("At least one public download port is required.")
        if len(value) != len(set(value)):
            raise ValueError("Public download ports must be unique.")
        if any(port < 1 or port > 65535 for port in value):
            raise ValueError("Public download ports must be between 1 and 65535.")
        return value

    @field_validator("firecrawl_remote_allowed_ports")
    @classmethod
    def validate_firecrawl_remote_allowed_ports(
        cls,
        value: tuple[int, ...],
    ) -> tuple[int, ...]:
        if not value:
            raise ValueError("At least one remote Firecrawl port is required.")
        if len(value) != len(set(value)):
            raise ValueError("Remote Firecrawl ports must be unique.")
        if any(port < 1 or port > 65535 for port in value):
            raise ValueError("Remote Firecrawl ports must be between 1 and 65535.")
        return value

    @field_validator("oidc_provider_label")
    @classmethod
    def validate_oidc_provider_label(cls, value: str) -> str:
        normalized = value.strip()
        if (
            not 1 <= len(normalized) <= 40
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 .()'_\-]*", normalized) is None
        ):
            raise ValueError("The OpenID Connect provider label must be bounded plain text.")
        return normalized

    @model_validator(mode="after")
    def validate_storage_settings(self) -> "Settings":
        if (
            self.storage_backend is not StorageBackend.S3
            and self.storage_signed_url_endpoint is not None
        ):
            raise ValueError("The signed URL endpoint requires S3 durable storage.")
        if (
            self.deployment_mode is DeploymentMode.SELF_HOSTED
            and self.storage_backend is not StorageBackend.FILESYSTEM
        ):
            raise ValueError("Self-hosted mode requires filesystem durable storage.")
        if (
            self.deployment_mode is DeploymentMode.HOSTED
            and self.storage_backend is not StorageBackend.S3
        ):
            raise ValueError("Hosted mode requires S3 durable storage.")

        if self.storage_backend is StorageBackend.S3:
            if not self.storage_region or not self.storage_region.strip():
                raise ValueError("S3 durable storage requires a region.")
            if not self.storage_bucket or not self.storage_bucket.strip():
                raise ValueError("S3 durable storage requires a bucket.")
            if (
                self.deployment_mode is DeploymentMode.HOSTED
                and self.storage_endpoint is not None
                and self.storage_endpoint.scheme != "https"
            ):
                raise ValueError("Hosted mode requires an HTTPS S3 endpoint.")
            signed_endpoint = self.storage_signed_url_endpoint
            if signed_endpoint is not None:
                if (
                    self.deployment_mode is DeploymentMode.HOSTED
                    and signed_endpoint.scheme != "https"
                ):
                    raise ValueError("The hosted signed URL endpoint must use HTTPS.")
                if (
                    signed_endpoint.username is not None
                    or signed_endpoint.password is not None
                    or signed_endpoint.path not in (None, "", "/")
                    or signed_endpoint.query is not None
                    or signed_endpoint.fragment is not None
                ):
                    raise ValueError("The signed URL endpoint contains unsafe URL components.")

        has_access_key = self.storage_access_key_file is not None
        has_secret_key = self.storage_secret_key_file is not None
        if has_access_key != has_secret_key:
            raise ValueError("Storage access and secret key files must be configured together.")
        if self.storage_session_token_file is not None and not (has_access_key and has_secret_key):
            raise ValueError(
                "A storage session token requires explicit access and secret key files."
            )

        for path in (
            self.storage_access_key_file,
            self.storage_secret_key_file,
            self.storage_session_token_file,
        ):
            if path is not None and (not path.is_file() or not os.access(path, os.R_OK)):
                raise ValueError("A storage credential path is not a regular readable file.")
        if self.storage_ca_bundle_file is not None and (
            not self.storage_ca_bundle_file.is_file()
            or not os.access(self.storage_ca_bundle_file, os.R_OK)
        ):
            raise ValueError("The storage CA bundle must be a regular readable file.")
        return self

    @model_validator(mode="after")
    def validate_hosted_identity_settings(self) -> "Settings":
        if self.deployment_mode is not DeploymentMode.HOSTED:
            return self
        if self.public_origin.scheme != "https":
            raise ValueError("Hosted mode requires an HTTPS public origin.")
        if not self.session_cookie_secure:
            raise ValueError("Hosted mode requires secure session cookies.")
        if self.oidc_issuer is None or self.oidc_issuer.scheme != "https":
            raise ValueError("Hosted mode requires an HTTPS OpenID Connect issuer.")
        if not self.oidc_client_id:
            raise ValueError("Hosted mode requires an OpenID Connect client identifier.")
        if self.oidc_client_secret_file is None or not self.oidc_client_secret_file.is_file():
            raise ValueError("Hosted mode requires an OpenID Connect client secret file.")
        if self.oidc_ca_bundle_file is not None and not self.oidc_ca_bundle_file.is_file():
            raise ValueError("The OpenID Connect CA bundle file is missing.")
        if self.oidc_redirect_uri is None:
            raise ValueError("Hosted mode requires an OpenID Connect redirect URI.")
        if self.oidc_redirect_uri.scheme != "https":
            raise ValueError("Hosted mode requires an HTTPS OpenID Connect redirect URI.")
        if (
            self.oidc_redirect_uri.scheme,
            self.oidc_redirect_uri.host,
            self.oidc_redirect_uri.port,
        ) != (
            self.public_origin.scheme,
            self.public_origin.host,
            self.public_origin.port,
        ):
            raise ValueError("The OpenID Connect redirect must use the public origin.")
        return self

    @model_validator(mode="after")
    def validate_credential_encryption_settings(self) -> "Settings":
        has_key_file = self.credential_encryption_key_file is not None
        has_key_id = self.credential_encryption_key_id is not None
        if has_key_file != has_key_id:
            raise ValueError(
                "The credential encryption key file and identifier must be configured together."
            )
        if not has_key_file:
            return self
        key_id = self.credential_encryption_key_id
        if key_id is None or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", key_id) is None:
            raise ValueError("The credential encryption key identifier is invalid.")
        self.credential_encryption_key()
        return self

    @model_validator(mode="after")
    def validate_firecrawl_mode(self) -> "Settings":
        if self.deployment_mode is DeploymentMode.HOSTED and self.firecrawl_base_url is not None:
            raise ValueError("Hosted mode does not accept a global Firecrawl base URL.")
        return self

    @model_validator(mode="after")
    def validate_firecrawl_remote_policy(self) -> "Settings":
        try:
            NetworkPolicy(
                allowed_public_ports=self.firecrawl_remote_allowed_ports,
                private_allowlist=self.firecrawl_remote_private_allowlist,
            )
        except (AcquisitionError, ValueError):
            raise ValueError("The remote Firecrawl destination policy is invalid.") from None
        return self

    def database_url(self) -> str:
        if not self.db_password_file.is_file():
            raise ValueError("Database password file is missing.")
        password = self.db_password_file.read_text(encoding="utf-8").strip()
        if len(password) < 32:
            raise ValueError("Database password must contain at least 32 characters.")
        return URL.create(
            "postgresql+psycopg",
            username=self.db_user,
            password=password,
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
        ).render_as_string(hide_password=False)

    def session_signing_secret(self) -> bytes:
        if not self.session_signing_secret_file.is_file():
            raise ValueError("Session signing secret file is missing.")
        secret = self.session_signing_secret_file.read_bytes().strip()
        if len(secret) < 32:
            raise ValueError("Session signing secret must contain at least 32 bytes.")
        return secret

    def discovery_fingerprint_secret(self) -> bytes:
        path = self.discovery_fingerprint_secret_file
        if not path.is_file() or not os.access(path, os.R_OK):
            raise ValueError("Discovery fingerprint secret file is missing.")
        secret = path.read_bytes().strip()
        if len(secret) < 32:
            raise ValueError("Discovery fingerprint secret must contain at least 32 bytes.")
        return secret

    def oidc_client_secret(self) -> str:
        if self.oidc_client_secret_file is None or not self.oidc_client_secret_file.is_file():
            raise ValueError("OpenID Connect client secret file is missing.")
        secret = self.oidc_client_secret_file.read_bytes().strip()
        if len(secret) < 32:
            raise ValueError("OpenID Connect client secret must contain at least 32 bytes.")
        try:
            return secret.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError("OpenID Connect client secret must be UTF-8 text.") from error

    def credential_encryption_key(self) -> bytes:
        path = self.credential_encryption_key_file
        if path is None or self.credential_encryption_key_id is None:
            raise ValueError("A credential encryption key file and identifier are required.")
        try:
            if not path.is_file() or not os.access(path, os.R_OK) or path.stat().st_size != 44:
                raise ValueError("The credential encryption key file is invalid.")
            encoded = path.read_bytes()
            decoded = base64.b64decode(encoded, validate=True)
        except (OSError, binascii.Error):
            raise ValueError("The credential encryption key file is invalid.") from None
        if len(decoded) != 32 or base64.b64encode(decoded) != encoded:
            raise ValueError(
                "The credential encryption key must contain one canonical 256-bit key."
            )
        return decoded
