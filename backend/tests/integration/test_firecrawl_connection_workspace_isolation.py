from datetime import timedelta

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine
from test_firecrawl_connection_repository import (
    CONNECTION_A,
    CONNECTION_B,
    NOW,
    SCOPE_A,
    SCOPE_B,
    WORKSPACE_A,
    WORKSPACE_B,
    database_engine,
    draft,
    vault,
)

from parserium_collector.adapters.firecrawl.contracts import (
    MetadataSearchRequest,
    MetadataSearchResult,
)
from parserium_collector.features.analysis.models import AnalysisSearchRequest
from parserium_collector.features.analysis.repository import PostgresAnalysisRepository
from parserium_collector.features.discovery.models import DocumentDiscoveryResponse
from parserium_collector.features.discovery.scoped import ScopedDiscoveryResult
from parserium_collector.features.firecrawl_connections.errors import (
    ConnectionNotFoundError,
)
from parserium_collector.features.firecrawl_connections.models import (
    ConnectionStatus,
    ConnectionType,
    ConnectionUpdate,
    CreateConnectionRequest,
    ResolvedConnection,
    UpdateConnectionRequest,
    ValidationOutcome,
)
from parserium_collector.features.firecrawl_connections.repository import (
    PostgresFirecrawlConnectionRepository,
)
from parserium_collector.features.firecrawl_connections.service import (
    FirecrawlConnectionService,
)

__all__ = ["database_engine"]


class AcceptingExecutor:
    def __init__(self) -> None:
        self.credentials: list[str] = []

    async def search(
        self,
        connection: ResolvedConnection,
        request: MetadataSearchRequest,
    ) -> MetadataSearchResult:
        del request
        self.credentials.append(connection.credential)
        return MetadataSearchResult(search_id="workspace-contract", results=[])


async def test_workspace_identifier_cannot_cross_repository_boundaries(
    database_engine: AsyncEngine,
) -> None:
    repository = PostgresFirecrawlConnectionRepository(database_engine)
    await repository.create_connection(
        SCOPE_A,
        await draft(CONNECTION_A, name="Workspace A only"),
        NOW,
    )
    replacement = await vault().encrypt(
        "workspace-b-test-only",
        WORKSPACE_B,
        CONNECTION_A,
        2,
    )

    assert await repository.get_connection(WORKSPACE_B, CONNECTION_A) is None
    assert await repository.list_connections(WORKSPACE_B) == ()
    assert (
        await repository.update_metadata(
            SCOPE_B,
            CONNECTION_A,
            ConnectionUpdate(name="Cross tenant", normalized_name="cross tenant"),
            NOW + timedelta(seconds=1),
        )
        is None
    )
    assert (
        await repository.replace_credential(
            SCOPE_B,
            CONNECTION_A,
            replacement,
            1,
            NOW + timedelta(seconds=2),
        )
        is None
    )
    assert (
        await repository.record_validation(
            WORKSPACE_B,
            CONNECTION_A,
            1,
            ValidationOutcome(
                succeeded=True,
                capability_profile={"contract": "metadata-search-v2"},
                failure_category=None,
            ),
            NOW + timedelta(seconds=3),
        )
        is None
    )
    assert not await repository.tombstone(
        SCOPE_B,
        CONNECTION_A,
        NOW + timedelta(seconds=4),
    )
    assert await repository.get_connection(WORKSPACE_A, CONNECTION_A) is not None


async def test_analysis_snapshot_is_tenant_safe_and_contains_no_connection_secret(
    database_engine: AsyncEngine,
) -> None:
    connections = PostgresFirecrawlConnectionRepository(database_engine)
    analysis = PostgresAnalysisRepository(database_engine)
    await connections.create_connection(
        SCOPE_A,
        await draft(CONNECTION_A, name="Workspace A only"),
        NOW,
    )
    result = ScopedDiscoveryResult(
        response=DocumentDiscoveryResponse(
            provider_search_ids=["safe-search-id"],
            candidates=[],
            rejected_non_document_results=0,
        ),
        connection_id=CONNECTION_A,
        connection_name_snapshot="Workspace A only",
        connection_type_snapshot=ConnectionType.CLOUD,
    )

    session, _ = await analysis.create_analysis_session(
        SCOPE_A,
        AnalysisSearchRequest(query="investment tables"),
        result,
        NOW + timedelta(seconds=1),
        NOW + timedelta(hours=1),
        10_000_000,
    )

    assert session.firecrawl_connection_id == CONNECTION_A
    assert session.firecrawl_connection_name_snapshot == "Workspace A only"
    assert session.firecrawl_connection_type_snapshot is ConnectionType.CLOUD
    assert "credential" not in str(session).lower()
    with pytest.raises(IntegrityError):
        await analysis.create_analysis_session(
            SCOPE_B,
            AnalysisSearchRequest(query="cross workspace tables"),
            result,
            NOW + timedelta(seconds=2),
            NOW + timedelta(hours=1),
            10_000_000,
        )


async def test_service_lifecycle_stays_isolated_across_two_workspaces(
    database_engine: AsyncEngine,
) -> None:
    repository = PostgresFirecrawlConnectionRepository(database_engine)
    executor = AcceptingExecutor()
    identifiers = iter((CONNECTION_A, CONNECTION_B))
    service = FirecrawlConnectionService(
        repository,
        vault(),
        executor,
        connection_id_factory=lambda: next(identifiers),
        remote_allowed_ports=(9444,),
    )
    created_a = await service.create_connection(
        SCOPE_A,
        CreateConnectionRequest(
            name="Workspace A TLS remote",
            connection_type=ConnectionType.REMOTE,
            base_url="https://test-firecrawl:9444",
            credential="workspace-a-test-only",
            is_default=True,
        ),
        NOW,
    )
    created_b = await service.create_connection(
        SCOPE_B,
        CreateConnectionRequest(
            name="Workspace B Cloud",
            connection_type=ConnectionType.CLOUD,
            credential="workspace-b-test-only",
            is_default=True,
        ),
        NOW + timedelta(seconds=1),
    )

    assert created_a.status is ConnectionStatus.HEALTHY
    assert created_b.status is ConnectionStatus.HEALTHY
    assert [item.id for item in (await service.list_connections(SCOPE_A)).connections] == [
        CONNECTION_A
    ]
    assert [item.id for item in (await service.list_connections(SCOPE_B)).connections] == [
        CONNECTION_B
    ]

    for operation in (
        lambda: service.test_connection(SCOPE_B, CONNECTION_A, NOW),
        lambda: service.update_connection(
            SCOPE_B,
            CONNECTION_A,
            UpdateConnectionRequest(name="Cross workspace"),
            NOW,
        ),
        lambda: service.replace_credential(
            SCOPE_B,
            CONNECTION_A,
            "cross-workspace-test-only",
            NOW,
        ),
        lambda: service.delete_connection(SCOPE_B, CONNECTION_A, NOW),
        lambda: service.resolve_connection(SCOPE_B, CONNECTION_A),
    ):
        with pytest.raises(ConnectionNotFoundError):
            await operation()

    rotated_a = await service.replace_credential(
        SCOPE_A,
        CONNECTION_A,
        "workspace-a-rotated-test-only",
        NOW + timedelta(seconds=2),
    )
    disabled_a = await service.update_connection(
        SCOPE_A,
        CONNECTION_A,
        UpdateConnectionRequest(enabled=False),
        NOW + timedelta(seconds=3),
    )
    assert rotated_a.status is ConnectionStatus.HEALTHY
    assert disabled_a.status is ConnectionStatus.DISABLED
    assert (await service.list_connections(SCOPE_B)).connections[0].id == CONNECTION_B

    await service.delete_connection(SCOPE_A, CONNECTION_A, NOW + timedelta(seconds=4))
    assert (await service.list_connections(SCOPE_A)).connections == ()
    assert (await service.list_connections(SCOPE_B)).connections[0].id == CONNECTION_B
    assert executor.credentials == [
        "workspace-a-test-only",
        "workspace-b-test-only",
        "workspace-a-rotated-test-only",
    ]
