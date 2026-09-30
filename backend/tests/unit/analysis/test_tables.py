from sqlalchemy import CheckConstraint, ForeignKey, Numeric

from parserium_collector.adapters.database.tables import metadata


def constraint_sql(table_name: str) -> dict[str, str]:
    table = metadata.tables[table_name]
    return {
        constraint.name or "": str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }


def foreign_key_targets(table_name: str) -> set[str]:
    return {
        element.target_fullname
        for constraint in metadata.tables[table_name].foreign_key_constraints
        for element in constraint.elements
        if isinstance(element, ForeignKey)
    }


def test_analysis_metadata_has_owned_sessions_candidates_and_tables() -> None:
    session = metadata.tables["discovery_analysis_sessions"]
    candidate = metadata.tables["candidate_analyses"]
    table = metadata.tables["candidate_tables"]

    assert foreign_key_targets(session.name) == {
        "firecrawl_connections.id",
        "firecrawl_connections.workspace_id",
        "local_sessions.token_digest",
        "users.id",
        "workspaces.id",
    }
    assert foreign_key_targets(candidate.name) >= {
        "discovery_analysis_sessions.id",
        "documents.id",
        "workspaces.id",
    }
    assert foreign_key_targets(table.name) >= {
        "candidate_analyses.id",
        "workspaces.id",
    }
    assert session.c.expires_at.type.timezone is True
    assert candidate.c.lease_expires_at.type.timezone is True
    assert candidate.c.expires_at.type.timezone is True
    assert all(isinstance(table.c[name].type, Numeric) for name in ("x", "y", "width", "height"))


def test_analysis_metadata_enforces_status_counts_coordinates_and_indexes() -> None:
    session_checks = constraint_sql("discovery_analysis_sessions")
    candidate_checks = constraint_sql("candidate_analyses")
    table_checks = constraint_sql("candidate_tables")

    assert "queued" in session_checks["ck_analysis_sessions_status"]
    assert "cancelled" in session_checks["ck_analysis_sessions_status"]
    assert "bytes_downloaded >= 0" in session_checks["ck_analysis_sessions_bytes"]
    assert "candidate_count >= 0" in session_checks["ck_analysis_sessions_candidates"]
    assert "promoted" in candidate_checks["ck_candidate_analyses_status"]
    assert (
        "analyzed_page_count <= page_count" in candidate_checks["ck_candidate_analyses_page_counts"]
    )
    assert "table_count >= 0" in candidate_checks["ck_candidate_analyses_table_count"]
    assert "x >= 0" in table_checks["ck_candidate_tables_coordinates"]
    assert "width > 0" in table_checks["ck_candidate_tables_dimensions"]

    assert {index.name for index in metadata.tables["candidate_analyses"].indexes} >= {
        "ix_candidate_analyses_claim",
        "ix_candidate_analyses_session_ordinal",
        "ix_candidate_analyses_expiry",
    }
    assert {index.name for index in metadata.tables["candidate_tables"].indexes} >= {
        "ix_candidate_tables_candidate_page",
    }


def test_durable_discovery_session_metadata_matches_the_migration() -> None:
    workspace = metadata.tables["workspaces"]
    session = metadata.tables["discovery_analysis_sessions"]

    assert workspace.c.discovery_concurrency_limit.nullable is False
    assert str(workspace.c.discovery_concurrency_limit.server_default.arg) == "1"
    assert (
        "discovery_concurrency_limit >= 1"
        in constraint_sql("workspaces")["ck_workspaces_discovery_concurrency_limit"]
    )
    assert (
        "discovery_concurrency_limit <= 5"
        in constraint_sql("workspaces")["ck_workspaces_discovery_concurrency_limit"]
    )
    assert set(session.c.keys()) == {
        "id",
        "workspace_id",
        "owner_session_digest",
        "created_by_user_id",
        "firecrawl_connection_id",
        "firecrawl_connection_name_snapshot",
        "firecrawl_connection_type_snapshot",
        "query",
        "document_types",
        "include_domains",
        "exclude_domains",
        "tables_required",
        "provider_search_ids",
        "status",
        "candidate_count",
        "session_byte_limit",
        "bytes_downloaded",
        "cancellation_requested",
        "error_code",
        "error_detail",
        "created_at",
        "updated_at",
        "expires_at",
        "completed_at",
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
    assert session.c.request_fingerprint.type.length == 64
    assert session.c.request_fingerprint_version.nullable
    assert session.c.result_limit.nullable
    assert session.c.job_stage.type.length == 16
    assert session.c.job_stage.nullable is False
    assert session.c.cache_reusable_until.type.timezone is True
    assert session.c.discovery_claimed_by.type.length == 128
    assert session.c.discovery_lease_expires_at.type.timezone is True
    assert session.c.provider_request_started_at.type.timezone is True
    assert session.c.firecrawl_credential_revision_snapshot.nullable
    assert session.c.creation_reason.type.length == 16
    assert session.c.creation_reason.nullable is False
    assert "parent_session_id" not in session.c

    checks = constraint_sql(session.name)
    assert "^[0-9a-f]{64}$" in checks["ck_analysis_sessions_request_fingerprint"]
    assert "request_fingerprint IS NOT NULL" in checks["ck_analysis_sessions_request_fingerprint"]
    assert (
        "request_fingerprint_version IS NOT NULL"
        in checks["ck_analysis_sessions_request_fingerprint"]
    )
    assert "request_fingerprint_version > 0" in checks["ck_analysis_sessions_request_fingerprint"]
    assert "result_limit BETWEEN 1 AND 30" in checks["ck_analysis_sessions_result_limit"]
    assert (
        "firecrawl_credential_revision_snapshot > 0"
        in checks["ck_analysis_sessions_credential_revision_snapshot"]
    )
    assert "force_refresh" in checks["ck_analysis_sessions_creation_reason"]
    assert (
        "status = 'queued' AND job_stage = 'queued'" in checks["ck_analysis_sessions_status_stage"]
    )
    assert (
        "status = 'running' AND job_stage IN ('discovering', 'analyzing')"
        in checks["ck_analysis_sessions_status_stage"]
    )
    assert (
        "status = 'completed' AND job_stage = 'completed'"
        in checks["ck_analysis_sessions_status_stage"]
    )
    assert (
        "discovery_claimed_by IS NULL AND discovery_lease_expires_at IS NULL"
        in checks["ck_analysis_sessions_discovery_ownership"]
    )
    assert (
        "provider_request_started_at IS NULL OR status <> 'queued'"
        in checks["ck_analysis_sessions_provider_request_ownership"]
    )
    assert "completed_at <= cache_reusable_until" in checks["ck_analysis_sessions_cache_reuse"]
    assert "cache_reusable_until <= expires_at" in checks["ck_analysis_sessions_cache_reuse"]

    indexes = {index.name: index for index in session.indexes}
    assert indexes["uq_analysis_sessions_active_fingerprint"].unique
    active_fingerprint_predicate = str(
        indexes["uq_analysis_sessions_active_fingerprint"].dialect_options["postgresql"]["where"]
    )
    assert "status IN ('queued', 'running')" in active_fingerprint_predicate
    assert "request_fingerprint IS NOT NULL" in active_fingerprint_predicate
    assert {
        "ix_analysis_sessions_discovery_claim",
        "ix_analysis_sessions_cache_reuse",
    } <= indexes.keys()


def test_discovery_job_events_and_links_metadata_are_workspace_scoped() -> None:
    events = metadata.tables["discovery_job_events"]
    links = metadata.tables["discovery_job_links"]

    assert set(events.c.keys()) == {
        "id",
        "workspace_id",
        "session_id",
        "actor_kind",
        "actor_user_id",
        "event_type",
        "safe_metadata",
        "created_at",
    }
    assert events.c.session_id.nullable
    assert events.c.safe_metadata.nullable is False
    assert events.c.created_at.type.timezone is True
    event_targets = foreign_key_targets(events.name)
    assert {
        "workspaces.id",
        "users.id",
        "discovery_analysis_sessions.id",
        "discovery_analysis_sessions.workspace_id",
    } <= event_targets
    event_checks = constraint_sql(events.name)
    assert "worker" in event_checks["ck_discovery_job_events_actor_kind"]
    for event_type in (
        "job_created",
        "active_reused",
        "cached_reused",
        "force_refresh_created",
        "job_claimed",
        "provider_request_started",
        "analysis_started",
        "job_completed",
        "job_failed",
        "cancellation_requested",
        "job_cancelled",
        "retry_created",
        "concurrency_updated",
    ):
        assert event_type in event_checks["ck_discovery_job_events_event_type"]
    assert "ix_discovery_job_events_workspace_created" in {index.name for index in events.indexes}

    assert set(links.c.keys()) == {
        "child_session_id",
        "workspace_id",
        "parent_session_id",
        "reason",
        "created_at",
    }
    assert links.c.child_session_id.primary_key
    link_targets = foreign_key_targets(links.name)
    assert {
        "discovery_analysis_sessions.id",
        "discovery_analysis_sessions.workspace_id",
    } <= link_targets
    link_checks = constraint_sql(links.name)
    assert "force_refresh" in link_checks["ck_discovery_job_links_reason"]
    assert (
        "child_session_id <> parent_session_id"
        in link_checks["ck_discovery_job_links_distinct_sessions"]
    )
    assert "ix_discovery_job_links_parent" in {index.name for index in links.indexes}
