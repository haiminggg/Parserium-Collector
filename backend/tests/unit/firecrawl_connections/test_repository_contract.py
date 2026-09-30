from inspect import signature

from parserium_collector.features.firecrawl_connections.repository import (
    FirecrawlConnectionRepository,
    PostgresFirecrawlConnectionRepository,
)

EXPECTED_METHODS = {
    "create_connection",
    "get_connection",
    "get_usable_connection_for_job",
    "list_connections",
    "list_envelopes_for_rewrap",
    "record_validation",
    "replace_credential",
    "replace_wrapped_keys",
    "tombstone",
    "update_metadata",
}


def test_postgres_repository_implements_the_connection_contract() -> None:
    assert {
        name
        for name, value in FirecrawlConnectionRepository.__dict__.items()
        if callable(value) and not name.startswith("_")
    } == EXPECTED_METHODS
    for method_name in EXPECTED_METHODS:
        assert signature(getattr(PostgresFirecrawlConnectionRepository, method_name)) == (
            signature(getattr(FirecrawlConnectionRepository, method_name))
        )
