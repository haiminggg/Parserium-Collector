from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from parserium_collector.adapters.database.tables import metadata


def test_durable_discovery_jobs_migration_is_the_only_schema_head() -> None:
    backend_root = Path(__file__).resolve().parents[2]
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "migrations"))

    scripts = ScriptDirectory.from_config(config)

    assert scripts.get_heads() == ["0009_unified_activity_history"]
    assert scripts.get_revision("0009_unified_activity_history").down_revision == (
        "0008_durable_discovery_jobs"
    )


def test_activity_history_is_soft_deleted_without_removing_artifacts() -> None:
    for table_name in (
        "discovery_analysis_sessions",
        "candidate_analyses",
        "collection_jobs",
        "document_exports",
    ):
        assert "history_deleted_at" in metadata.tables[table_name].c
    assert "created_by_user_id" in metadata.tables["collection_jobs"].c
    assert "created_by_user_id" in metadata.tables["document_exports"].c


def test_database_metadata_mirrors_hosted_tenancy_schema() -> None:
    assert {
        "users",
        "workspaces",
        "oidc_identities",
        "workspace_memberships",
        "workspace_invitations",
        "hosted_sessions",
    } <= set(metadata.tables)
    for table_name in (
        "local_sessions",
        "documents",
        "collection_jobs",
        "document_exports",
        "discovery_analysis_sessions",
        "candidate_analyses",
        "candidate_tables",
    ):
        assert "workspace_id" in metadata.tables[table_name].c
        assert metadata.tables[table_name].c.workspace_id.nullable is False
    assert metadata.tables["discovery_analysis_sessions"].c.owner_session_digest.nullable
    assert metadata.tables["discovery_analysis_sessions"].c.created_by_user_id.nullable


def test_database_metadata_mirrors_artifact_registry_schema() -> None:
    assert {
        "artifact_objects",
        "artifact_references",
        "workspace_storage_usage",
    } <= set(metadata.tables)
    assert "deleted_at" in metadata.tables["documents"].c
    assert "storage_key" not in metadata.tables["documents"].c
    for column_name in (
        "temporary_storage_key",
        "converted_pdf_storage_key",
        "preview_storage_key",
    ):
        assert column_name not in metadata.tables["candidate_analyses"].c

    objects = metadata.tables["artifact_objects"]
    assert objects.c.workspace_id.nullable is False
    assert objects.c.size_bytes.nullable
    assert objects.c.sha256.nullable
    references = metadata.tables["artifact_references"]
    assert references.c.workspace_id.nullable is False
    assert references.c.artifact_object_id.nullable is False
