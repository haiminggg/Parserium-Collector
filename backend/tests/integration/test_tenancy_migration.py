from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from parserium_collector.settings import Settings

DOCUMENT_ID = UUID("10000000-0000-4000-8000-000000000001")
JOB_ID = UUID("20000000-0000-4000-8000-000000000001")
EXPORT_ID = UUID("30000000-0000-4000-8000-000000000001")
SESSION_ID = UUID("40000000-0000-4000-8000-000000000001")
CANDIDATE_ID = UUID("50000000-0000-4000-8000-000000000001")
TABLE_ID = UUID("60000000-0000-4000-8000-000000000001")
OTHER_WORKSPACE_ID = UUID("70000000-0000-4000-8000-000000000001")
LOCAL_SESSION_DIGEST = "1" * 64
NOW = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)


def _config() -> Config:
    backend_root = Path(__file__).resolve().parents[2]
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "migrations"))
    return config


def _insert_revision_0004_graph(engine: object) -> None:
    with engine.begin() as connection:  # type: ignore[union-attr]
        connection.execute(
            text(
                "INSERT INTO local_sessions "
                "(token_digest, created_at, last_seen_at, revoked_at) "
                "VALUES (:digest, :now, :now, NULL)"
            ),
            {"digest": LOCAL_SESSION_DIGEST, "now": NOW},
        )
        connection.execute(
            text(
                "INSERT INTO documents "
                "(id, sha256, document_type, media_type, size_bytes, storage_key, "
                "safe_filename, created_at) VALUES "
                "(:id, :sha256, 'pdf', 'application/pdf', 5, :storage_key, "
                "'report.pdf', :now)"
            ),
            {
                "id": DOCUMENT_ID,
                "sha256": "a" * 64,
                "storage_key": "documents/aa/report.pdf",
                "now": NOW,
            },
        )
        connection.execute(
            text(
                "INSERT INTO collection_jobs "
                "(id, source_url, title, expected_document_type, status, attempt_count, "
                "available_at, claimed_by, lease_expires_at, bytes_downloaded, "
                "content_length, document_id, error_code, error_detail, error_retryable, "
                "created_at, updated_at, started_at, completed_at) VALUES "
                "(:id, 'https://documents.example/report.pdf', 'Report', 'pdf', "
                "'completed', 1, :now, NULL, NULL, 5, 5, :document_id, NULL, NULL, "
                "NULL, :now, :now, :now, :now)"
            ),
            {"id": JOB_ID, "document_id": DOCUMENT_ID, "now": NOW},
        )
        connection.execute(
            text(
                "INSERT INTO document_exports "
                "(id, document_id, relative_directory, target_filename, "
                "exported_relative_path, status, attempt_count, available_at, claimed_by, "
                "lease_expires_at, error_code, error_detail, created_at, updated_at, "
                "completed_at) VALUES "
                "(:id, :document_id, 'reports', 'report.pdf', 'reports/report.pdf', "
                "'completed', 1, :now, NULL, NULL, NULL, NULL, :now, :now, :now)"
            ),
            {"id": EXPORT_ID, "document_id": DOCUMENT_ID, "now": NOW},
        )
        connection.execute(
            text(
                "INSERT INTO discovery_analysis_sessions "
                "(id, owner_session_digest, query, document_types, include_domains, "
                "exclude_domains, tables_required, provider_search_ids, status, "
                "candidate_count, session_byte_limit, bytes_downloaded, "
                "cancellation_requested, error_code, error_detail, created_at, updated_at, "
                "expires_at, completed_at) VALUES "
                "(:id, :digest, 'investment tables', CAST('[\"pdf\"]' AS JSON), "
                "CAST('[]' AS JSON), CAST('[]' AS JSON), TRUE, CAST('[]' AS JSON), "
                "'completed', 1, 1048576, 5, FALSE, NULL, NULL, :now, :now, :expires, :now)"
            ),
            {
                "id": SESSION_ID,
                "digest": LOCAL_SESSION_DIGEST,
                "now": NOW,
                "expires": NOW + timedelta(hours=1),
            },
        )
        connection.execute(
            text(
                "INSERT INTO candidate_analyses "
                "(id, session_id, ordinal, source_url, title, description, document_type, "
                "status, attempt_count, available_at, claimed_by, lease_expires_at, "
                "bytes_downloaded, content_length, sha256, media_type, safe_filename, "
                "temporary_storage_key, converted_pdf_storage_key, preview_storage_key, "
                "page_count, analyzed_page_count, table_count, table_count_lower_bound, "
                "preview_page_num, preview_width, preview_height, error_code, error_detail, "
                "error_retryable, promoted_document_id, created_at, updated_at, started_at, "
                "completed_at, expires_at) VALUES "
                "(:id, :session_id, 0, 'https://documents.example/report.pdf', 'Report', "
                "NULL, 'pdf', 'promoted', 1, :now, NULL, NULL, 5, 5, :sha256, "
                "'application/pdf', 'report.pdf', 'analysis/temp.pdf', NULL, "
                "'analysis/preview.png', 1, 1, 1, FALSE, 1, 100, 100, NULL, NULL, NULL, "
                ":document_id, :now, :now, :now, :now, :expires)"
            ),
            {
                "id": CANDIDATE_ID,
                "session_id": SESSION_ID,
                "sha256": "a" * 64,
                "document_id": DOCUMENT_ID,
                "now": NOW,
                "expires": NOW + timedelta(hours=1),
            },
        )
        connection.execute(
            text(
                "INSERT INTO candidate_tables "
                "(id, candidate_analysis_id, page_num, table_index, x, y, width, height, "
                "cells, markdown, created_at) VALUES "
                "(:id, :candidate_id, 1, 0, 0, 0, 100, 100, CAST('[]' AS JSON), "
                "'| A |', :now)"
            ),
            {"id": TABLE_ID, "candidate_id": CANDIDATE_ID, "now": NOW},
        )


def test_tenancy_migration_preserves_graph_and_enforces_workspace_links() -> None:
    config = _config()
    engine = create_engine(Settings().database_url())
    command.downgrade(config, "base")
    try:
        command.upgrade(config, "0004_document_analysis")
        _insert_revision_0004_graph(engine)

        command.upgrade(config, "head")

        inspector = inspect(engine)
        for table_name in (
            "local_sessions",
            "documents",
            "collection_jobs",
            "document_exports",
            "discovery_analysis_sessions",
            "candidate_analyses",
            "candidate_tables",
        ):
            assert "workspace_id" in {
                column["name"] for column in inspector.get_columns(table_name)
            }

        with engine.begin() as connection:
            local_workspace_count = connection.scalar(
                text("SELECT count(*) FROM workspaces WHERE is_local IS TRUE")
            )
            ownership = connection.execute(
                text(
                    "SELECT d.workspace_id, j.workspace_id, e.workspace_id, "
                    "s.workspace_id, c.workspace_id, t.workspace_id "
                    "FROM documents d "
                    "JOIN collection_jobs j ON j.document_id = d.id "
                    "JOIN document_exports e ON e.document_id = d.id "
                    "JOIN candidate_analyses c ON c.promoted_document_id = d.id "
                    "JOIN discovery_analysis_sessions s ON s.id = c.session_id "
                    "JOIN candidate_tables t ON t.candidate_analysis_id = c.id"
                )
            ).one()
            assert local_workspace_count == 1
            assert len(set(ownership)) == 1
            connection.execute(
                text(
                    "INSERT INTO workspaces (id, name, is_local, created_at, updated_at) "
                    "VALUES (:id, 'Other', FALSE, :now, :now)"
                ),
                {"id": OTHER_WORKSPACE_ID, "now": NOW},
            )

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE document_exports SET workspace_id = :workspace_id "
                        "WHERE id = :export_id"
                    ),
                    {"workspace_id": OTHER_WORKSPACE_ID, "export_id": EXPORT_ID},
                )
    finally:
        engine.dispose()
        command.downgrade(config, "base")
