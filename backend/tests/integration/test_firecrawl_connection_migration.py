from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from parserium_collector.settings import Settings

SESSION_ID = UUID("20000000-0000-4000-8000-000000000007")
LOCAL_SESSION_DIGEST = "7" * 64
NOW = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)


def _config() -> Config:
    backend_root = Path(__file__).resolve().parents[2]
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "migrations"))
    return config


def _seed_revision_0006_analysis_session(engine: object) -> None:
    with engine.begin() as connection:  # type: ignore[union-attr]
        workspace_id = connection.scalar(text("SELECT id FROM workspaces WHERE is_local IS TRUE"))
        assert workspace_id is not None
        connection.execute(
            text(
                "INSERT INTO local_sessions "
                "(token_digest, workspace_id, created_at, last_seen_at, revoked_at) "
                "VALUES (:digest, :workspace_id, :now, :now, NULL)"
            ),
            {
                "digest": LOCAL_SESSION_DIGEST,
                "workspace_id": workspace_id,
                "now": NOW,
            },
        )
        connection.execute(
            text(
                "INSERT INTO discovery_analysis_sessions "
                "(id, workspace_id, owner_session_digest, created_by_user_id, query, "
                "document_types, include_domains, exclude_domains, tables_required, "
                "provider_search_ids, status, candidate_count, session_byte_limit, "
                "bytes_downloaded, cancellation_requested, error_code, error_detail, "
                "created_at, updated_at, expires_at, completed_at) VALUES "
                "(:id, :workspace_id, :digest, NULL, 'investment tables', "
                "CAST('[\"pdf\"]' AS JSON), CAST('[]' AS JSON), CAST('[]' AS JSON), "
                "TRUE, CAST('[]' AS JSON), 'completed', 0, 1048576, 0, FALSE, NULL, "
                "NULL, :now, :now, :expires, :now)"
            ),
            {
                "id": SESSION_ID,
                "workspace_id": workspace_id,
                "digest": LOCAL_SESSION_DIGEST,
                "now": NOW,
                "expires": NOW + timedelta(hours=1),
            },
        )


def test_firecrawl_connection_migration_preserves_existing_analysis_sessions() -> None:
    config = _config()
    engine = create_engine(Settings().database_url())
    command.downgrade(config, "base")
    try:
        command.upgrade(config, "0006_cloud_artifact_storage")
        _seed_revision_0006_analysis_session(engine)

        command.upgrade(config, "0007_firecrawl_connections")

        inspector = inspect(engine)
        assert "firecrawl_connections" in inspector.get_table_names()
        session_columns = {
            column["name"] for column in inspector.get_columns("discovery_analysis_sessions")
        }
        assert {
            "firecrawl_connection_id",
            "firecrawl_connection_name_snapshot",
            "firecrawl_connection_type_snapshot",
        } <= session_columns
        with engine.begin() as connection:
            upgraded = connection.execute(
                text(
                    "SELECT query, firecrawl_connection_id, "
                    "firecrawl_connection_name_snapshot, "
                    "firecrawl_connection_type_snapshot "
                    "FROM discovery_analysis_sessions WHERE id = :id"
                ),
                {"id": SESSION_ID},
            ).one()
            assert upgraded.query == "investment tables"
            assert upgraded.firecrawl_connection_id is None
            assert upgraded.firecrawl_connection_name_snapshot is None
            assert upgraded.firecrawl_connection_type_snapshot is None

        command.downgrade(config, "0006_cloud_artifact_storage")

        inspector = inspect(engine)
        assert "firecrawl_connections" not in inspector.get_table_names()
        downgraded_columns = {
            column["name"] for column in inspector.get_columns("discovery_analysis_sessions")
        }
        assert {
            "firecrawl_connection_id",
            "firecrawl_connection_name_snapshot",
            "firecrawl_connection_type_snapshot",
        }.isdisjoint(downgraded_columns)
        with engine.begin() as connection:
            assert (
                connection.scalar(
                    text("SELECT query FROM discovery_analysis_sessions WHERE id = :id"),
                    {"id": SESSION_ID},
                )
                == "investment tables"
            )
    finally:
        engine.dispose()
        command.downgrade(config, "base")
