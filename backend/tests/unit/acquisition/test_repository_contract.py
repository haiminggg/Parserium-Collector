from inspect import signature

from parserium_collector.features.acquisition.repository import (
    AcquisitionRepository,
    PostgresAcquisitionRepository,
)

EXPECTED_METHODS = {
    "claim_collection_job",
    "claim_export",
    "complete_collection",
    "complete_export",
    "create_collection_jobs",
    "create_export",
    "delete_document",
    "delete_completed_collection_jobs",
    "fail_collection",
    "fail_export",
    "get_collection_job",
    "get_document",
    "list_collection_jobs",
    "list_collection_jobs_page",
    "list_documents",
    "list_documents_page",
    "list_exports",
    "mark_collection_validating",
    "renew_collection_lease",
    "renew_export_lease",
    "retry_collection_job",
    "update_collection_progress",
}


def test_postgres_repository_implements_the_acquisition_contract() -> None:
    for method_name in EXPECTED_METHODS:
        protocol_method = getattr(AcquisitionRepository, method_name)
        implementation_method = getattr(PostgresAcquisitionRepository, method_name)

        assert callable(protocol_method)
        assert signature(implementation_method) == signature(protocol_method)
