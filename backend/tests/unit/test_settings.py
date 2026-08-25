from pathlib import Path
from secrets import token_urlsafe

import pytest
from sqlalchemy import make_url

from parserium_collector.settings import Settings


def test_default_expected_migration_matches_schema_head() -> None:
    assert Settings().expected_migration == "0002_local_sessions"


def test_firecrawl_search_timeout_has_a_bounded_default() -> None:
    assert Settings().firecrawl_search_timeout_seconds == 45.0

    with pytest.raises(ValueError):
        Settings(firecrawl_search_timeout_seconds=0)
    with pytest.raises(ValueError):
        Settings(firecrawl_search_timeout_seconds=121)


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
