from pathlib import Path
from secrets import token_urlsafe

from parserium_collector.adapters.database.engine import create_engine
from parserium_collector.adapters.database.tables import worker_heartbeats
from parserium_collector.settings import Settings


async def test_create_engine_uses_the_protected_database_secret(tmp_path: Path) -> None:
    secret = token_urlsafe(32)
    secret_file = tmp_path / "db_password"
    secret_file.write_text(secret, encoding="utf-8")
    settings = Settings(
        db_password_file=secret_file,
        db_host="db.internal",
        db_name="collector_test",
        db_user="collector_user",
    )

    engine = create_engine(settings)
    try:
        assert engine.url.drivername == "postgresql+psycopg"
        assert engine.url.username == "collector_user"
        assert engine.url.password == secret
        assert engine.url.host == "db.internal"
        assert engine.url.database == "collector_test"
    finally:
        await engine.dispose()


def test_worker_heartbeat_table_has_expected_primary_key_and_timestamp() -> None:
    assert worker_heartbeats.primary_key.columns.keys() == ["worker_id"]
    assert worker_heartbeats.c.last_seen_at.nullable is False
    assert worker_heartbeats.c.last_seen_at.type.timezone is True
