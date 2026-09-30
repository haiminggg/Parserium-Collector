from datetime import UTC, datetime
from uuid import UUID

import pytest

from parserium_collector.adapters.firecrawl.contracts import (
    MetadataSearchRequest,
    MetadataSearchResult,
    SearchResult,
)
from parserium_collector.adapters.firecrawl.errors import FirecrawlAdapterError
from parserium_collector.features.analysis.models import DiscoverySelection
from parserium_collector.features.discovery.models import DocumentDiscoveryRequest
from parserium_collector.features.discovery.scoped import (
    HostedScopedDiscovery,
    SelfHostedScopedDiscovery,
)
from parserium_collector.features.firecrawl_connections.errors import (
    ConnectionNotFoundError,
)
from parserium_collector.features.firecrawl_connections.executor import (
    ConnectionExecutionError,
)
from parserium_collector.features.firecrawl_connections.models import (
    ConnectionFailureCategory,
    ConnectionType,
    ResolvedConnection,
)
from parserium_collector.features.identity.models import WorkspaceRole, WorkspaceScope

NOW = datetime(2026, 9, 2, 15, 0, tzinfo=UTC)
WORKSPACE_A = UUID("10000000-0000-4000-8000-000000000018")
WORKSPACE_B = UUID("10000000-0000-4000-8000-000000000028")
USER_A = UUID("20000000-0000-4000-8000-000000000018")
CONNECTION_A = UUID("30000000-0000-4000-8000-000000000018")
SCOPE_A = WorkspaceScope(WORKSPACE_A, USER_A, WorkspaceRole.OWNER)


class GlobalDiscoveryTestDouble:
    def __init__(self) -> None:
        self.requests: list[DocumentDiscoveryRequest] = []

    async def search(self, request: DocumentDiscoveryRequest):
        from parserium_collector.features.discovery.models import DocumentDiscoveryResponse

        self.requests.append(request)
        return DocumentDiscoveryResponse(
            provider_search_ids=["global-search"],
            candidates=[],
            rejected_non_document_results=0,
        )


class ConnectionServiceTestDouble:
    def __init__(self) -> None:
        self.resolved = ResolvedConnection(
            id=CONNECTION_A,
            workspace_id=WORKSPACE_A,
            name="Workspace cloud",
            connection_type=ConnectionType.CLOUD,
            normalized_base_url=None,
            credential="test-only-token",
            credential_revision=3,
        )
        self.resolve_calls: list[tuple[WorkspaceScope, UUID | None]] = []
        self.prepare_calls: list[tuple[WorkspaceScope, UUID | None]] = []
        self.resolve_job_calls: list[tuple[UUID, DiscoverySelection]] = []
        self.fail_resolution = False
        self.failure_calls: list[
            tuple[ResolvedConnection, ConnectionFailureCategory, datetime]
        ] = []

    async def resolve_connection(
        self,
        scope: WorkspaceScope,
        connection_id: UUID | None,
    ) -> ResolvedConnection:
        self.resolve_calls.append((scope, connection_id))
        if self.fail_resolution or scope.workspace_id != WORKSPACE_A:
            raise ConnectionNotFoundError
        return self.resolved

    async def prepare_discovery(
        self,
        scope: WorkspaceScope,
        request: DocumentDiscoveryRequest,
    ) -> DiscoverySelection:
        self.prepare_calls.append((scope, request.firecrawl_connection_id))
        if self.fail_resolution or scope.workspace_id != WORKSPACE_A:
            raise ConnectionNotFoundError
        return DiscoverySelection(
            connection_id=self.resolved.id,
            connection_name=self.resolved.name,
            connection_type=self.resolved.connection_type,
            credential_revision=self.resolved.credential_revision,
            provider_identity="test-only-provider-identity",
        )

    async def resolve_connection_for_job(
        self,
        workspace_id: UUID,
        selection: DiscoverySelection,
    ) -> ResolvedConnection:
        self.resolve_job_calls.append((workspace_id, selection))
        if self.fail_resolution or workspace_id != WORKSPACE_A:
            raise ConnectionNotFoundError
        return self.resolved

    async def record_execution_failure(
        self,
        connection: ResolvedConnection,
        category: ConnectionFailureCategory,
        now: datetime,
    ) -> None:
        self.failure_calls.append((connection, category, now))


class ExecutorTestDouble:
    def __init__(self) -> None:
        self.calls: list[tuple[ResolvedConnection, MetadataSearchRequest]] = []
        self.failure: ConnectionFailureCategory | None = None

    async def search(
        self,
        connection: ResolvedConnection,
        request: MetadataSearchRequest,
    ) -> MetadataSearchResult:
        self.calls.append((connection, request))
        if self.failure is not None:
            raise ConnectionExecutionError(self.failure)
        document_type = "pdf" if "filetype:pdf" in request.query else "docx"
        return MetadataSearchResult(
            search_id=f"workspace-{document_type}",
            results=[
                SearchResult(
                    url=f"https://bank.example/report.{document_type}",
                    title=f"Report {document_type}",
                    description=None,
                )
            ],
        )


async def test_self_hosted_uses_global_provider_and_rejects_workspace_selector() -> None:
    global_service = GlobalDiscoveryTestDouble()
    scoped = SelfHostedScopedDiscovery(global_service, "http://firecrawl:3002")

    request = DocumentDiscoveryRequest(query="bank report", document_types=("pdf",))
    selection = await scoped.prepare(SCOPE_A, request)

    assert global_service.requests == []
    assert selection.connection_id is None
    assert selection.connection_name is None
    assert selection.connection_type is None
    assert selection.credential_revision is None
    assert "http://firecrawl:3002" in selection.provider_identity
    assert not hasattr(selection, "credential")

    result = await scoped.search_prepared(WORKSPACE_A, request, selection, NOW)

    assert result.response.provider_search_ids == ["global-search"]
    assert result.connection_id is None
    assert result.connection_name_snapshot is None
    assert result.connection_type_snapshot is None
    with pytest.raises(ValueError, match="not supported"):
        await scoped.search(
            SCOPE_A,
            DocumentDiscoveryRequest(
                query="bank report",
                firecrawl_connection_id=CONNECTION_A,
            ),
            NOW,
        )
    assert len(global_service.requests) == 1


async def test_hosted_resolves_selected_or_default_connection_and_returns_safe_snapshots() -> None:
    connections = ConnectionServiceTestDouble()
    executor = ExecutorTestDouble()
    scoped = HostedScopedDiscovery(connections, executor)

    selected_request = DocumentDiscoveryRequest(
        query="bank report",
        document_types=("pdf", "docx"),
        firecrawl_connection_id=CONNECTION_A,
    )
    selection = await scoped.prepare(SCOPE_A, selected_request)

    assert executor.calls == []
    assert not hasattr(selection, "credential")

    selected = await scoped.search_prepared(
        WORKSPACE_A,
        selected_request,
        selection,
        NOW,
    )
    defaulted = await scoped.search(
        SCOPE_A,
        DocumentDiscoveryRequest(query="bank report", document_types=("pdf",)),
        NOW,
    )

    assert connections.resolve_calls == []
    assert connections.prepare_calls == [(SCOPE_A, CONNECTION_A), (SCOPE_A, None)]
    assert connections.resolve_job_calls == [
        (WORKSPACE_A, selection),
        (WORKSPACE_A, selection),
    ]
    assert selected.connection_id == CONNECTION_A
    assert selected.connection_name_snapshot == "Workspace cloud"
    assert selected.connection_type_snapshot is ConnectionType.CLOUD
    assert selected.response.provider_search_ids == ["workspace-pdf", "workspace-docx"]
    assert defaulted.response.provider_search_ids == ["workspace-pdf"]
    assert len(executor.calls) == 3
    assert all(call[0].credential == "test-only-token" for call in executor.calls)


async def test_hosted_rejects_foreign_connection_before_executor_receives_credential() -> None:
    connections = ConnectionServiceTestDouble()
    connections.fail_resolution = True
    executor = ExecutorTestDouble()
    scoped = HostedScopedDiscovery(connections, executor)

    with pytest.raises(ConnectionNotFoundError):
        await scoped.search(
            WorkspaceScope(WORKSPACE_B, USER_A, WorkspaceRole.OWNER),
            DocumentDiscoveryRequest(
                query="bank report",
                firecrawl_connection_id=CONNECTION_A,
            ),
            NOW,
        )

    assert executor.calls == []


async def test_hosted_records_safe_connection_failure_and_exposes_adapter_error() -> None:
    connections = ConnectionServiceTestDouble()
    executor = ExecutorTestDouble()
    executor.failure = ConnectionFailureCategory.RATE_LIMITED
    scoped = HostedScopedDiscovery(connections, executor)

    with pytest.raises(FirecrawlAdapterError) as raised:
        await scoped.search(
            SCOPE_A,
            DocumentDiscoveryRequest(query="bank report", document_types=("pdf",)),
            NOW,
        )

    assert raised.value.code == "rate_limited"
    assert raised.value.retryable is True
    assert connections.failure_calls == [
        (connections.resolved, ConnectionFailureCategory.RATE_LIMITED, NOW)
    ]
