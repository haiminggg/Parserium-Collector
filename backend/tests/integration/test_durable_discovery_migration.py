import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import URL, bindparam, create_engine, inspect, text

NOW = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)
LEGACY_SESSIONS = {
    UUID("80000000-0000-4000-8000-000000000001"): "queued",
    UUID("80000000-0000-4000-8000-000000000002"): "running",
    UUID("80000000-0000-4000-8000-000000000003"): "completed",
    UUID("80000000-0000-4000-8000-000000000004"): "cancelled",
    UUID("80000000-0000-4000-8000-000000000005"): "failed",
}


def _isolated_database_url(monkeypatch: pytest.MonkeyPatch) -> str:
    required = (
        "TEST_DATABASE_HOST",
        "TEST_DATABASE_NAME",
        "TEST_DATABASE_PASSWORD_FILE",
    )
    if any(not os.environ.get(name) for name in required):
        pytest.skip(
            "destructive migration test requires explicit TEST_DATABASE_HOST, "
            "TEST_DATABASE_NAME, and TEST_DATABASE_PASSWORD_FILE"
        )
    password_file = Path(os.environ["TEST_DATABASE_PASSWORD_FILE"])
    password = password_file.read_text(encoding="utf-8").strip()
    port = int(os.environ.get("TEST_DATABASE_PORT", "5432"))
    user = os.environ.get("TEST_DATABASE_USER", "parserium_collector")
    database = os.environ["TEST_DATABASE_NAME"]
    monkeypatch.setenv("DASHBOARD_DB_HOST", os.environ["TEST_DATABASE_HOST"])
    monkeypatch.setenv("DASHBOARD_DB_PORT", str(port))
    monkeypatch.setenv("DASHBOARD_DB_NAME", database)
    monkeypatch.setenv("DASHBOARD_DB_USER", user)
    monkeypatch.setenv("DASHBOARD_DB_PASSWORD_FILE", str(password_file))
    return URL.create(
        "postgresql+psycopg",
        username=user,
        password=password,
        host=os.environ["TEST_DATABASE_HOST"],
        port=port,
        database=database,
    ).render_as_string(hide_password=False)


def _config() -> Config:
    backend_root = Path(__file__).resolve().parents[2]
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "migrations"))
    return config


def _seed_revision_0007_sessions(engine: object) -> None:
    with engine.begin() as connection:  # type: ignore[union-attr]
        workspace_id = connection.scalar(text("SELECT id FROM workspaces WHERE is_local IS TRUE"))
        assert workspace_id is not None
        for session_id, status in LEGACY_SESSIONS.items():
            completed_at = NOW if status in {"completed", "cancelled", "failed"} else None
            connection.execute(
                text(
                    "INSERT INTO discovery_analysis_sessions "
                    "(id, workspace_id, owner_session_digest, created_by_user_id, "
                    "firecrawl_connection_id, firecrawl_connection_name_snapshot, "
                    "firecrawl_connection_type_snapshot, query, document_types, "
                    "include_domains, exclude_domains, tables_required, provider_search_ids, "
                    "status, candidate_count, session_byte_limit, bytes_downloaded, "
                    "cancellation_requested, error_code, error_detail, created_at, updated_at, "
                    "expires_at, completed_at) VALUES "
                    "(:id, :workspace_id, NULL, NULL, NULL, NULL, NULL, :query, "
                    "CAST('[\"pdf\"]' AS JSON), CAST('[]' AS JSON), CAST('[]' AS JSON), "
                    "TRUE, CAST('[]' AS JSON), :status, 0, 1048576, 0, FALSE, NULL, NULL, "
                    ":now, :now, :expires_at, :completed_at)"
                ),
                {
                    "id": session_id,
                    "workspace_id": workspace_id,
                    "query": f"legacy {status}",
                    "status": status,
                    "now": NOW,
                    "expires_at": NOW + timedelta(hours=1),
                    "completed_at": completed_at,
                },
            )


def test_durable_discovery_migration_backfills_and_downgrades_safely(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = _isolated_database_url(monkeypatch)
    config = _config()
    engine = create_engine(database_url)
    test_failed = False
    try:
        command.downgrade(config, "base")
        command.upgrade(config, "0007_firecrawl_connections")
        _seed_revision_0007_sessions(engine)

        command.upgrade(config, "0008_durable_discovery_jobs")

        inspector = inspect(engine)
        assert {"discovery_job_events", "discovery_job_links"} <= set(inspector.get_table_names())
        workspace_columns = {column["name"] for column in inspector.get_columns("workspaces")}
        assert "discovery_concurrency_limit" in workspace_columns
        session_columns = {
            column["name"] for column in inspector.get_columns("discovery_analysis_sessions")
        }
        phase_four_columns = {
            "request_fingerprint",
            "request_fingerprint_version",
            "result_limit",
            "job_stage",
            "cache_reusable_until",
            "discovery_claimed_by",
            "discovery_lease_expires_at",
            "provider_request_started_at",
            "firecrawl_credential_revision_snapshot",
            "creation_reason",
        }
        assert phase_four_columns <= session_columns

        with engine.begin() as connection:
            assert (
                connection.scalar(
                    text(
                        "SELECT discovery_concurrency_limit FROM workspaces WHERE is_local IS TRUE"
                    )
                )
                == 1
            )
            upgraded = {
                row.id: row
                for row in connection.execute(
                    text(
                        "SELECT id, status, job_stage, creation_reason, request_fingerprint, "
                        "request_fingerprint_version, result_limit "
                        "FROM discovery_analysis_sessions"
                    )
                )
            }
            assert upgraded[next(iter(LEGACY_SESSIONS))].status == "running"
            assert upgraded[next(iter(LEGACY_SESSIONS))].job_stage == "analyzing"
            expected_stages = {
                "running": "analyzing",
                "completed": "completed",
                "cancelled": "cancelled",
                "failed": "failed",
            }
            for session_id, legacy_status in LEGACY_SESSIONS.items():
                row = upgraded[session_id]
                expected_status = "running" if legacy_status == "queued" else legacy_status
                assert row.status == expected_status
                assert row.job_stage == expected_stages[expected_status]
                assert row.creation_reason == "initial"
                assert row.request_fingerprint is None
                assert row.request_fingerprint_version is None
                assert row.result_limit is None
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM discovery_analysis_sessions "
                        "WHERE job_stage = 'queued'"
                    )
                )
                == 0
            )
            failed_session_id = next(
                session_id for session_id, status in LEGACY_SESSIONS.items() if status == "failed"
            )
            connection.execute(
                text(
                    "UPDATE discovery_analysis_sessions "
                    "SET provider_request_started_at = :now, discovery_claimed_by = NULL, "
                    "discovery_lease_expires_at = NULL WHERE id = :id"
                ),
                {"id": failed_session_id, "now": NOW},
            )
            retained_marker = connection.execute(
                text(
                    "SELECT provider_request_started_at, discovery_claimed_by, "
                    "discovery_lease_expires_at FROM discovery_analysis_sessions "
                    "WHERE id = :id"
                ),
                {"id": failed_session_id},
            ).one()
            assert retained_marker.provider_request_started_at == NOW
            assert retained_marker.discovery_claimed_by is None
            assert retained_marker.discovery_lease_expires_at is None

        command.downgrade(config, "0007_firecrawl_connections")

        inspector = inspect(engine)
        assert {"discovery_job_events", "discovery_job_links"}.isdisjoint(
            inspector.get_table_names()
        )
        assert "discovery_concurrency_limit" not in {
            column["name"] for column in inspector.get_columns("workspaces")
        }
        assert phase_four_columns.isdisjoint(
            column["name"] for column in inspector.get_columns("discovery_analysis_sessions")
        )
        with engine.begin() as connection:
            assert (
                connection.scalar(
                    text("SELECT status FROM discovery_analysis_sessions WHERE id = :id"),
                    {"id": next(iter(LEGACY_SESSIONS))},
                )
                == "running"
            )
    except BaseException:
        test_failed = True
        raise
    finally:
        try:
            if inspect(engine).has_table("discovery_analysis_sessions"):
                with engine.begin() as connection:
                    connection.execute(
                        text(
                            "DELETE FROM discovery_analysis_sessions WHERE id IN :session_ids"
                        ).bindparams(bindparam("session_ids", expanding=True)),
                        {"session_ids": tuple(LEGACY_SESSIONS)},
                    )
            command.downgrade(config, "base")
        except Exception:
            if not test_failed:
                raise
        finally:
            engine.dispose()
