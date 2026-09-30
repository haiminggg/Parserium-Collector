from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from parserium_collector.settings import Settings

DOCUMENT_A = UUID("10000000-0000-4000-8000-000000000001")
DOCUMENT_B = UUID("10000000-0000-4000-8000-000000000002")
WORKSPACE_B = UUID("20000000-0000-4000-8000-000000000001")
SESSION_A = UUID("30000000-0000-4000-8000-000000000001")
CANDIDATE_A = UUID("40000000-0000-4000-8000-000000000001")
NOW = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
SHARED_ANALYSIS_KEY = "workspaces/legacy/analysis/shared-artifact"
OWNER_SESSION_DIGEST = "d" * 64


def _config() -> Config:
    backend_root = Path(__file__).resolve().parents[2]
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "migrations"))
    return config


def _seed_revision_0005(engine: object) -> tuple[UUID, UUID]:
    with engine.begin() as connection:  # type: ignore[union-attr]
        workspace_a = connection.scalar(text("SELECT id FROM workspaces WHERE is_local IS TRUE"))
        assert isinstance(workspace_a, UUID)
        connection.execute(
            text(
                "INSERT INTO workspaces (id, name, is_local, created_at, updated_at) "
                "VALUES (:id, 'Workspace B', FALSE, :now, :now)"
            ),
            {"id": WORKSPACE_B, "now": NOW},
        )
        connection.execute(
            text(
                "INSERT INTO documents "
                "(id, workspace_id, sha256, document_type, media_type, size_bytes, "
                "storage_key, safe_filename, created_at) VALUES "
                "(:id_a, :workspace_a, :sha_a, 'pdf', 'application/pdf', 11, :key_a, "
                "'a.pdf', :now), "
                "(:id_b, :workspace_b, :sha_b, 'docx', :docx_media, 17, :key_b, "
                "'b.docx', :now)"
            ),
            {
                "id_a": DOCUMENT_A,
                "workspace_a": workspace_a,
                "sha_a": "a" * 64,
                "key_a": f"workspaces/{workspace_a}/documents/aa/a.pdf",
                "id_b": DOCUMENT_B,
                "workspace_b": WORKSPACE_B,
                "sha_b": "b" * 64,
                "docx_media": (
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                ),
                "key_b": f"workspaces/{WORKSPACE_B}/documents/bb/b.docx",
                "now": NOW,
            },
        )
        connection.execute(
            text(
                "INSERT INTO local_sessions "
                "(token_digest, workspace_id, created_at, last_seen_at, revoked_at) "
                "VALUES (:token_digest, :workspace_id, :now, :now, NULL)"
            ),
            {
                "token_digest": OWNER_SESSION_DIGEST,
                "workspace_id": workspace_a,
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
                "(:id, :workspace_id, :owner_session_digest, NULL, 'tables', "
                "CAST('[\"pdf\"]' AS JSON), "
                "CAST('[]' AS JSON), CAST('[]' AS JSON), TRUE, CAST('[]' AS JSON), "
                "'completed', 1, 1048576, 11, FALSE, NULL, NULL, :now, :now, :expires, :now)"
            ),
            {
                "id": SESSION_A,
                "workspace_id": workspace_a,
                "owner_session_digest": OWNER_SESSION_DIGEST,
                "now": NOW,
                "expires": NOW + timedelta(hours=1),
            },
        )
        connection.execute(
            text(
                "INSERT INTO candidate_analyses "
                "(id, workspace_id, session_id, ordinal, source_url, title, description, "
                "document_type, status, attempt_count, available_at, claimed_by, "
                "lease_expires_at, bytes_downloaded, content_length, sha256, media_type, "
                "safe_filename, temporary_storage_key, converted_pdf_storage_key, "
                "preview_storage_key, page_count, analyzed_page_count, table_count, "
                "table_count_lower_bound, preview_page_num, preview_width, preview_height, "
                "error_code, error_detail, error_retryable, promoted_document_id, created_at, "
                "updated_at, started_at, completed_at, expires_at) VALUES "
                "(:id, :workspace_id, :session_id, 0, 'https://example.invalid/a.pdf', "
                "'A', NULL, 'pdf', 'ready', 1, :now, NULL, NULL, 11, 11, :sha256, "
                "'application/pdf', 'a.pdf', :shared_key, NULL, :shared_key, 1, 1, 1, "
                "FALSE, 1, 100, 100, NULL, NULL, NULL, NULL, :now, :now, :now, :now, "
                ":expires)"
            ),
            {
                "id": CANDIDATE_A,
                "workspace_id": workspace_a,
                "session_id": SESSION_A,
                "sha256": "c" * 64,
                "shared_key": SHARED_ANALYSIS_KEY,
                "now": NOW,
                "expires": NOW + timedelta(hours=1),
            },
        )
    return workspace_a, WORKSPACE_B


def test_artifact_migration_backfills_without_inventing_analysis_metadata() -> None:
    config = _config()
    engine = create_engine(Settings().database_url())
    command.downgrade(config, "base")
    try:
        command.upgrade(config, "0005_hosted_identity_tenancy")
        workspace_a, workspace_b = _seed_revision_0005(engine)

        command.upgrade(config, "0006_cloud_artifact_storage")

        inspector = inspect(engine)
        assert {
            "artifact_objects",
            "artifact_references",
            "workspace_storage_usage",
        } <= set(inspector.get_table_names())
        assert "storage_key" not in {
            column["name"] for column in inspector.get_columns("documents")
        }
        assert "deleted_at" in {column["name"] for column in inspector.get_columns("documents")}
        candidate_columns = {
            column["name"] for column in inspector.get_columns("candidate_analyses")
        }
        assert {
            "temporary_storage_key",
            "converted_pdf_storage_key",
            "preview_storage_key",
        }.isdisjoint(candidate_columns)

        with engine.begin() as connection:
            document_objects = connection.execute(
                text(
                    "SELECT workspace_id, state, size_bytes, sha256, media_type "
                    "FROM artifact_objects WHERE state = 'available' ORDER BY workspace_id"
                )
            ).all()
            assert len(document_objects) == 2
            assert {(row.size_bytes, row.sha256) for row in document_objects} == {
                (11, "a" * 64),
                (17, "b" * 64),
            }
            legacy = connection.execute(
                text(
                    "SELECT id, state, size_bytes, sha256 FROM artifact_objects "
                    "WHERE storage_key = :key"
                ),
                {"key": SHARED_ANALYSIS_KEY},
            ).one()
            assert legacy.state == "legacy_pending"
            assert legacy.size_bytes is None
            assert legacy.sha256 is None
            shared_reference_count = connection.scalar(
                text(
                    "SELECT count(*) FROM artifact_references WHERE artifact_object_id = :object_id"
                ),
                {"object_id": legacy.id},
            )
            assert shared_reference_count == 2
            usage = dict(
                connection.execute(
                    text(
                        "SELECT workspace_id, retained_bytes FROM workspace_storage_usage "
                        "WHERE workspace_id IN (:workspace_a, :workspace_b)"
                    ),
                    {"workspace_a": workspace_a, "workspace_b": workspace_b},
                ).all()
            )
            assert usage == {workspace_a: 11, workspace_b: 17}

        command.downgrade(config, "0005_hosted_identity_tenancy")
        inspector = inspect(engine)
        assert "artifact_objects" not in inspector.get_table_names()
        assert "deleted_at" not in {column["name"] for column in inspector.get_columns("documents")}
        assert "storage_key" in {column["name"] for column in inspector.get_columns("documents")}
        with engine.begin() as connection:
            restored = connection.execute(
                text(
                    "SELECT temporary_storage_key, preview_storage_key "
                    "FROM candidate_analyses WHERE id = :id"
                ),
                {"id": CANDIDATE_A},
            ).one()
            assert restored.temporary_storage_key == SHARED_ANALYSIS_KEY
            assert restored.preview_storage_key == SHARED_ANALYSIS_KEY

        command.upgrade(config, "0006_cloud_artifact_storage")
        with engine.begin() as connection:
            assert connection.scalar(text("SELECT count(*) FROM artifact_objects")) == 3
    finally:
        engine.dispose()
        command.downgrade(config, "base")
