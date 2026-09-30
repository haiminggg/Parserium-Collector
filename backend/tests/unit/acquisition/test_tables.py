import pytest

from parserium_collector.adapters.database import tables


def acquisition_table(name: str):
    if name not in tables.metadata.tables:
        pytest.fail(f"{name} table is not implemented")
    return tables.metadata.tables[name]


def test_acquisition_tables_are_registered_in_metadata() -> None:
    assert {
        "documents",
        "collection_jobs",
        "document_exports",
    }.issubset(tables.metadata.tables)


def test_documents_have_unique_content_and_registry_managed_storage() -> None:
    documents = acquisition_table("documents")

    assert documents.c.sha256.type.length == 64
    assert any(
        constraint.name == "uq_documents_workspace_sha256"
        and [column.name for column in constraint.columns] == ["workspace_id", "sha256"]
        for constraint in documents.constraints
    )
    assert "storage_key" not in documents.c
    assert documents.c.deleted_at.nullable is True


def test_collection_jobs_reference_documents_and_have_a_claim_index() -> None:
    jobs = acquisition_table("collection_jobs")

    assert list(jobs.c.document_id.foreign_keys)[0].target_fullname == "documents.id"
    assert any(
        [column.name for column in index.columns] == ["status", "available_at"]
        for index in jobs.indexes
    )
    check_sql = " ".join(
        str(constraint.sqltext) for constraint in jobs.constraints if hasattr(constraint, "sqltext")
    )
    for status in ("queued", "downloading", "validating", "completed", "duplicate", "failed"):
        assert status in check_sql


def test_document_exports_reference_documents_and_have_a_claim_index() -> None:
    exports = acquisition_table("document_exports")

    assert list(exports.c.document_id.foreign_keys)[0].target_fullname == "documents.id"
    assert any(
        [column.name for column in index.columns] == ["status", "available_at"]
        for index in exports.indexes
    )
    check_sql = " ".join(
        str(constraint.sqltext)
        for constraint in exports.constraints
        if hasattr(constraint, "sqltext")
    )
    for status in ("queued", "exporting", "completed", "failed"):
        assert status in check_sql
