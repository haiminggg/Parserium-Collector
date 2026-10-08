from inspect import signature

from parserium_collector.features.analysis.repository import (
    AnalysisRepository,
    PostgresAnalysisRepository,
)

EXPECTED_METHODS = {
    "cancel_analysis_session",
    "claim_candidate_analysis",
    "claim_expired_candidate_cleanup",
    "complete_candidate_analysis",
    "create_analysis_session",
    "delete_expired_candidate_cleanup",
    "fail_candidate_analysis",
    "get_analysis_progress",
    "get_analysis_session",
    "get_workspace_candidate_analysis",
    "list_candidate_analyses",
    "list_candidate_tables",
    "mark_candidate_promoted",
    "promote_candidate_to_collection",
    "renew_candidate_lease",
    "transition_candidate_stage",
    "update_candidate_progress",
}


def test_postgres_repository_implements_the_analysis_contract() -> None:
    for method_name in EXPECTED_METHODS:
        protocol_method = getattr(AnalysisRepository, method_name)
        implementation_method = getattr(PostgresAnalysisRepository, method_name)

        assert callable(protocol_method)
        assert signature(implementation_method) == signature(protocol_method)
