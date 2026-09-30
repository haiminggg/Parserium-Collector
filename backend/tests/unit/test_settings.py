import base64
from pathlib import Path
from secrets import token_urlsafe

import pytest
from sqlalchemy import make_url

from parserium_collector.settings import Settings


def hosted_settings(tmp_path: Path, **overrides: object) -> Settings:
    client_secret = tmp_path / "oidc_client_secret"
    client_secret.write_text(token_urlsafe(48), encoding="utf-8")
    wrapping_key = tmp_path / "firecrawl_wrapping_key"
    wrapping_key.write_bytes(base64.b64encode(b"k" * 32))
    values: dict[str, object] = {
        "deployment_mode": "hosted",
        "public_origin": "https://app.parserium.test",
        "allowed_hosts": ("app.parserium.test",),
        "allowed_origins": ("https://app.parserium.test",),
        "session_cookie_secure": True,
        "oidc_issuer": "https://identity.parserium.test",
        "oidc_client_id": "parserium-web",
        "oidc_client_secret_file": client_secret,
        "oidc_redirect_uri": "https://app.parserium.test/api/v1/auth/callback",
        "storage_backend": "s3",
        "storage_endpoint": "https://minio.parserium.test",
        "storage_region": "us-east-1",
        "storage_bucket": "parserium-artifacts",
        "credential_encryption_key_file": wrapping_key,
        "credential_encryption_key_id": "hosted-v1",
    }
    values.update(overrides)
    return Settings(**values)


def test_hosted_mode_requires_https_secure_cookie_and_oidc(tmp_path: Path) -> None:
    assert hosted_settings(tmp_path).deployment_mode.value == "hosted"
    for invalid in (
        {"public_origin": "http://app.parserium.test"},
        {"session_cookie_secure": False},
        {"oidc_issuer": None},
        {"oidc_client_id": None},
        {"oidc_redirect_uri": "https://other.parserium.test/api/v1/auth/callback"},
    ):
        with pytest.raises(ValueError):
            hosted_settings(tmp_path, **invalid)


@pytest.mark.parametrize("label", ["", "  ", "<b>Google</b>", "G\nGoogle", "x" * 41])
def test_oidc_provider_label_is_bounded_plain_text(tmp_path: Path, label: str) -> None:
    with pytest.raises(ValueError, match="provider label"):
        hosted_settings(tmp_path, oidc_provider_label=label)


def test_self_hosted_mode_keeps_current_defaults() -> None:
    settings = Settings()

    assert settings.deployment_mode.value == "self_hosted"
    assert settings.storage_backend.value == "filesystem"
    assert settings.session_cookie_secure is False
    assert settings.oidc_issuer is None


def test_firecrawl_runtime_configuration_is_mode_specific(tmp_path: Path) -> None:
    self_hosted = Settings(firecrawl_base_url="http://firecrawl:3002")

    assert str(self_hosted.firecrawl_base_url).rstrip("/") == "http://firecrawl:3002"
    assert self_hosted.credential_encryption_key_file is None
    with pytest.raises(ValueError, match="global Firecrawl"):
        hosted_settings(tmp_path, firecrawl_base_url="https://firecrawl.example")


def test_self_hosted_mode_rejects_s3_durable_storage() -> None:
    with pytest.raises(ValueError, match="Self-hosted mode requires filesystem"):
        Settings(
            storage_backend="s3",
            storage_region="us-east-1",
            storage_bucket="parserium-artifacts",
        )


def test_hosted_mode_requires_s3_and_https_endpoint(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Hosted mode requires S3 durable storage"):
        hosted_settings(tmp_path, storage_backend="filesystem")

    with pytest.raises(ValueError, match="HTTPS"):
        hosted_settings(tmp_path, storage_endpoint="http://minio.parserium.test")


def test_hosted_signed_url_endpoint_is_optional_and_https(tmp_path: Path) -> None:
    assert hosted_settings(tmp_path).storage_signed_url_endpoint is None
    configured = hosted_settings(
        tmp_path,
        storage_signed_url_endpoint="https://localhost:9000",
    )

    assert str(configured.storage_signed_url_endpoint).rstrip("/") == ("https://localhost:9000")


def test_self_hosted_storage_rejects_a_signed_url_endpoint() -> None:
    with pytest.raises(ValueError, match="signed URL endpoint"):
        Settings(storage_signed_url_endpoint="https://localhost:9000")


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://localhost:9000",
        "https://user:secret@localhost:9000",
        "https://localhost:9000/private",
        "https://localhost:9000?token=value",
        "https://localhost:9000#fragment",
    ],
)
def test_hosted_signed_url_endpoint_rejects_unsafe_components(
    tmp_path: Path,
    endpoint: str,
) -> None:
    with pytest.raises(ValueError, match="signed URL endpoint"):
        hosted_settings(tmp_path, storage_signed_url_endpoint=endpoint)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"storage_region": None}, "region"),
        ({"storage_bucket": None}, "bucket"),
        ({"storage_bucket": ""}, "bucket"),
    ],
)
def test_s3_storage_requires_region_and_bucket(
    tmp_path: Path,
    overrides: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        hosted_settings(tmp_path, **overrides)


def test_s3_storage_uses_sdk_default_credentials_when_files_are_absent(tmp_path: Path) -> None:
    settings = hosted_settings(tmp_path)

    assert settings.storage_access_key_file is None
    assert settings.storage_secret_key_file is None
    assert settings.storage_session_token_file is None


def test_explicit_storage_credentials_must_be_paired(tmp_path: Path) -> None:
    access_key = tmp_path / "access-key"
    secret_key = tmp_path / "secret-key"
    session_token = tmp_path / "session-token"
    access_key.write_text("verification-access", encoding="utf-8")
    secret_key.write_text("verification-secret", encoding="utf-8")
    session_token.write_text("verification-session", encoding="utf-8")

    with pytest.raises(ValueError, match="configured together"):
        hosted_settings(tmp_path, storage_access_key_file=access_key)
    with pytest.raises(ValueError, match="configured together"):
        hosted_settings(tmp_path, storage_secret_key_file=secret_key)
    with pytest.raises(ValueError, match="requires explicit access and secret"):
        hosted_settings(tmp_path, storage_session_token_file=session_token)

    settings = hosted_settings(
        tmp_path,
        storage_access_key_file=access_key,
        storage_secret_key_file=secret_key,
        storage_session_token_file=session_token,
    )
    assert settings.storage_access_key_file == access_key
    assert settings.storage_secret_key_file == secret_key
    assert settings.storage_session_token_file == session_token


def test_storage_secret_and_ca_paths_must_be_regular_files(tmp_path: Path) -> None:
    directory = tmp_path / "not-a-file"
    directory.mkdir()
    with pytest.raises(ValueError, match="regular readable file"):
        hosted_settings(
            tmp_path,
            storage_access_key_file=directory,
            storage_secret_key_file=directory,
        )
    with pytest.raises(ValueError, match="CA bundle"):
        hosted_settings(tmp_path, storage_ca_bundle_file=directory)


@pytest.mark.parametrize("value", [29, 301])
def test_signed_url_ttl_is_bounded(value: int) -> None:
    with pytest.raises(ValueError):
        Settings(storage_signed_url_ttl_seconds=value)


def test_storage_and_scratch_settings_have_bounded_defaults() -> None:
    settings = Settings()

    assert settings.storage_signed_url_ttl_seconds == 60
    assert settings.storage_orphan_grace_seconds == 3600
    assert settings.storage_max_concurrency == 4
    assert settings.scratch_root == Path("/tmp/parserium-collector")  # noqa: S108
    assert settings.scratch_stale_seconds == 86400

    for values in (
        {"storage_orphan_grace_seconds": 59},
        {"storage_max_concurrency": 0},
        {"storage_max_concurrency": 33},
        {"scratch_stale_seconds": 299},
    ):
        with pytest.raises(ValueError):
            Settings(**values)


def test_oidc_client_secret_returns_valid_secret_bytes(tmp_path: Path) -> None:
    settings = hosted_settings(tmp_path)

    assert len(settings.oidc_client_secret()) >= 32


def test_oidc_client_secret_rejects_short_secret(tmp_path: Path) -> None:
    secret_file = tmp_path / "short_oidc_client_secret"
    secret_file.write_text("too-short", encoding="utf-8")

    with pytest.raises(ValueError, match="at least 32"):
        hosted_settings(tmp_path, oidc_client_secret_file=secret_file).oidc_client_secret()


def test_hosted_oidc_ca_bundle_must_reference_a_file(tmp_path: Path) -> None:
    missing_bundle = tmp_path / "missing-ca.pem"
    with pytest.raises(ValueError):
        hosted_settings(tmp_path, oidc_ca_bundle_file=missing_bundle)

    ca_bundle = tmp_path / "ca.pem"
    ca_bundle.write_text("verification CA", encoding="utf-8")
    assert hosted_settings(tmp_path, oidc_ca_bundle_file=ca_bundle).oidc_ca_bundle_file == (
        ca_bundle
    )


def test_default_expected_migration_matches_schema_head() -> None:
    assert Settings().expected_migration == "0009_unified_activity_history"


def test_credential_encryption_key_is_optional_until_the_vault_is_constructed() -> None:
    settings = Settings()

    assert settings.credential_encryption_key_file is None
    assert settings.credential_encryption_key_id is None
    with pytest.raises(ValueError, match="credential encryption key"):
        settings.credential_encryption_key()


def test_credential_encryption_key_settings_must_be_paired(tmp_path: Path) -> None:
    key_file = tmp_path / "wrapping-key"
    key_file.write_bytes(base64.b64encode(b"k" * 32))

    with pytest.raises(ValueError, match="configured together"):
        Settings(credential_encryption_key_file=key_file)
    with pytest.raises(ValueError, match="configured together"):
        Settings(credential_encryption_key_id="hosted-v1")


def test_credential_encryption_key_returns_strict_256_bit_key(tmp_path: Path) -> None:
    key_file = tmp_path / "wrapping-key"
    key_file.write_bytes(base64.b64encode(b"k" * 32))
    settings = Settings(
        credential_encryption_key_file=key_file,
        credential_encryption_key_id="hosted-v1",
    )

    assert settings.credential_encryption_key() == b"k" * 32


@pytest.mark.parametrize(
    "contents",
    [
        b"not-base64!",
        base64.b64encode(b"k" * 32) + b"\n",
        base64.b64encode(b"k" * 31),
        base64.b64encode(b"k" * 33),
    ],
)
def test_credential_encryption_key_rejects_invalid_files(
    tmp_path: Path,
    contents: bytes,
) -> None:
    key_file = tmp_path / "wrapping-key"
    key_file.write_bytes(contents)

    with pytest.raises(ValueError, match="credential encryption key"):
        Settings(
            credential_encryption_key_file=key_file,
            credential_encryption_key_id="hosted-v1",
        )


def test_credential_encryption_key_rejects_missing_file_and_directory(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "directory"
    directory.mkdir()

    for path in (tmp_path / "missing", directory):
        with pytest.raises(ValueError, match="credential encryption key"):
            Settings(
                credential_encryption_key_file=path,
                credential_encryption_key_id="hosted-v1",
            )


@pytest.mark.parametrize("key_id", ["", " key", "key\nvalue", "x" * 129])
def test_credential_encryption_key_rejects_unsafe_key_identifiers(
    tmp_path: Path,
    key_id: str,
) -> None:
    key_file = tmp_path / "wrapping-key"
    key_file.write_bytes(base64.b64encode(b"k" * 32))

    with pytest.raises(ValueError, match="key identifier"):
        Settings(
            credential_encryption_key_file=key_file,
            credential_encryption_key_id=key_id,
        )


def test_analysis_settings_have_bounded_defaults() -> None:
    settings = Settings()

    assert settings.analysis_session_max_bytes == 512 * 1024 * 1024
    assert settings.analysis_page_limit == 200
    assert settings.analysis_session_ttl_seconds == 3600
    assert settings.analysis_parser_timeout_seconds == 120.0
    assert settings.analysis_conversion_timeout_seconds == 120.0
    assert settings.analysis_lease_seconds == 60

    invalid_values = (
        {"analysis_session_max_bytes": 0},
        {"analysis_page_limit": 0},
        {"analysis_page_limit": 1001},
        {"analysis_session_ttl_seconds": 0},
        {"analysis_parser_timeout_seconds": 0},
        {"analysis_conversion_timeout_seconds": 0},
        {"analysis_lease_seconds": 0},
    )
    for values in invalid_values:
        with pytest.raises(ValueError):
            Settings(**values)


def test_acquisition_settings_have_bounded_defaults() -> None:
    settings = Settings()

    assert settings.download_max_bytes == 100 * 1024 * 1024
    assert settings.download_connect_timeout_seconds == 10.0
    assert settings.download_read_timeout_seconds == 60.0
    assert settings.download_total_timeout_seconds == 600.0
    assert settings.download_max_redirects == 5
    assert settings.download_max_attempts == 3
    assert settings.download_retry_base_seconds == 30
    assert settings.download_lease_seconds == 60
    assert settings.download_allowed_public_ports == (80, 443)
    assert settings.download_private_allowlist == ()
    assert settings.export_root == Path("/exports")
    assert settings.docx_max_expanded_bytes == 500 * 1024 * 1024
    assert settings.docx_max_expansion_ratio == 100.0

    invalid_values = (
        {"download_max_bytes": 0},
        {"download_connect_timeout_seconds": 0},
        {"download_read_timeout_seconds": 0},
        {"download_total_timeout_seconds": 0},
        {"download_max_redirects": 11},
        {"download_max_attempts": 0},
        {"download_retry_base_seconds": 0},
        {"download_lease_seconds": 0},
        {"docx_max_expanded_bytes": 0},
        {"docx_max_expansion_ratio": 0},
    )
    for values in invalid_values:
        with pytest.raises(ValueError):
            Settings(**values)


@pytest.mark.parametrize("ports", [(), (0,), (65536,), (80, 80)])
def test_acquisition_settings_reject_invalid_public_ports(ports: tuple[int, ...]) -> None:
    with pytest.raises(ValueError):
        Settings(download_allowed_public_ports=ports)


def test_firecrawl_search_timeout_has_a_bounded_default() -> None:
    assert Settings().firecrawl_search_timeout_seconds == 45.0

    with pytest.raises(ValueError):
        Settings(firecrawl_search_timeout_seconds=0)
    with pytest.raises(ValueError):
        Settings(firecrawl_search_timeout_seconds=121)


def test_remote_firecrawl_policy_has_bounded_secure_defaults() -> None:
    settings = Settings()

    assert settings.firecrawl_remote_allowed_ports == (443,)
    assert settings.firecrawl_remote_private_allowlist == ()
    assert settings.firecrawl_validation_timeout_seconds == 15.0
    assert settings.firecrawl_response_limit_bytes == 2 * 1024 * 1024


@pytest.mark.parametrize(
    "values",
    [
        {"firecrawl_remote_allowed_ports": ()},
        {"firecrawl_remote_allowed_ports": (0,)},
        {"firecrawl_remote_allowed_ports": (65536,)},
        {"firecrawl_remote_allowed_ports": (443, 443)},
        {"firecrawl_validation_timeout_seconds": 0},
        {"firecrawl_validation_timeout_seconds": 61},
        {"firecrawl_response_limit_bytes": 1023},
        {"firecrawl_response_limit_bytes": 8 * 1024 * 1024 + 1},
        {"firecrawl_remote_private_allowlist": ("*.example:443",)},
        {"firecrawl_remote_private_allowlist": ("10.0.0.0/8",)},
        {"firecrawl_remote_private_allowlist": ("example.com",)},
    ],
)
def test_remote_firecrawl_policy_rejects_unsafe_configuration(
    values: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        Settings(**values)


def test_database_url_preserves_secret_and_connection_fields(tmp_path: Path) -> None:
    secret = f"{token_urlsafe(32)}:/%@"
    secret_file = tmp_path / "db_password"
    secret_file.write_text(f"{secret}\n", encoding="utf-8")
    settings = Settings(
        db_password_file=secret_file,
        db_host="db.internal",
        db_port=5544,
        db_name="collector_test",
        db_user="collector_user",
    )

    url = make_url(settings.database_url())

    assert url.drivername == "postgresql+psycopg"
    assert url.username == "collector_user"
    assert url.password == secret
    assert url.host == "db.internal"
    assert url.port == 5544
    assert url.database == "collector_test"


def test_database_url_rejects_missing_secret_file(tmp_path: Path) -> None:
    settings = Settings(db_password_file=tmp_path / "missing")

    with pytest.raises(ValueError, match="missing"):
        settings.database_url()


def test_database_url_rejects_short_secret(tmp_path: Path) -> None:
    secret_file = tmp_path / "db_password"
    secret_file.write_text("too-short", encoding="utf-8")
    settings = Settings(db_password_file=secret_file)

    with pytest.raises(ValueError, match="at least 32"):
        settings.database_url()


def test_session_signing_secret_returns_valid_secret_bytes(tmp_path: Path) -> None:
    secret = token_urlsafe(48).encode("ascii")
    secret_file = tmp_path / "session_signing_secret"
    secret_file.write_bytes(secret)
    settings = Settings(session_signing_secret_file=secret_file)

    assert settings.session_signing_secret() == secret


def test_session_signing_secret_rejects_missing_file(tmp_path: Path) -> None:
    settings = Settings(session_signing_secret_file=tmp_path / "missing")

    with pytest.raises(ValueError, match="missing"):
        settings.session_signing_secret()


def test_session_signing_secret_rejects_short_secret(tmp_path: Path) -> None:
    secret_file = tmp_path / "session_signing_secret"
    secret_file.write_text("too-short", encoding="utf-8")
    settings = Settings(session_signing_secret_file=secret_file)

    with pytest.raises(ValueError, match="at least 32"):
        settings.session_signing_secret()
