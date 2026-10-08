import json
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from parserium_collector.adapters.firecrawl.contracts import (
    MetadataSearchRequest,
    MetadataSearchResult,
)
from parserium_collector.adapters.firecrawl.errors import FirecrawlAdapterError
from parserium_collector.features.analysis.models import DiscoverySelection
from parserium_collector.features.discovery.models import (
    DocumentDiscoveryRequest,
    DocumentDiscoveryResponse,
)
from parserium_collector.features.discovery.service import DiscoveryService
from parserium_collector.features.firecrawl_connections.executor import (
    ConnectionExecutionError,
    FirecrawlExecutor,
)
from parserium_collector.features.firecrawl_connections.models import (
    ConnectionFailureCategory,
    ConnectionType,
    ResolvedConnection,
)
from parserium_collector.features.identity.models import WorkspaceScope


@dataclass(frozen=True)
class ScopedDiscoveryResult:
    response: DocumentDiscoveryResponse
    connection_id: UUID | None
    connection_name_snapshot: str | None
    connection_type_snapshot: ConnectionType | None


class ScopedDiscoveryService(Protocol):
    async def prepare(
        self,
        scope: WorkspaceScope,
        request: DocumentDiscoveryRequest,
    ) -> DiscoverySelection: ...

    async def prepare_for_job(
        self,
        workspace_id: UUID,
        persisted: DiscoverySelection,
    ) -> DiscoverySelection: ...

    async def search_prepared(
        self,
        workspace_id: UUID,
        request: DocumentDiscoveryRequest,
        selection: DiscoverySelection,
        now: datetime,
    ) -> ScopedDiscoveryResult: ...

    async def search(
        self,
        scope: WorkspaceScope,
        request: DocumentDiscoveryRequest,
        now: datetime,
    ) -> ScopedDiscoveryResult: ...


class GlobalDiscoveryService(Protocol):
    async def search(self, request: DocumentDiscoveryRequest) -> DocumentDiscoveryResponse: ...


class ConnectionResolver(Protocol):
    async def prepare_discovery(
        self,
        scope: WorkspaceScope,
        request: DocumentDiscoveryRequest,
    ) -> DiscoverySelection: ...

    async def prepare_discovery_for_job(
        self,
        workspace_id: UUID,
        connection_id: UUID,
        revision: int,
    ) -> DiscoverySelection: ...

    async def resolve_connection_for_job(
        self,
        workspace_id: UUID,
        selection: DiscoverySelection,
    ) -> ResolvedConnection: ...

    async def resolve_connection(
        self,
        scope: WorkspaceScope,
        connection_id: UUID | None,
    ) -> ResolvedConnection: ...

    async def record_execution_failure(
        self,
        connection: ResolvedConnection,
        category: ConnectionFailureCategory,
        now: datetime,
    ) -> None: ...


@dataclass(frozen=True)
class _ResolvedMetadataProvider:
    executor: FirecrawlExecutor
    connection: ResolvedConnection

    async def probe_metadata_search(
        self,
        request: MetadataSearchRequest,
    ) -> MetadataSearchResult:
        return await self.executor.search(self.connection, request)


@dataclass(frozen=True)
class SelfHostedScopedDiscovery:
    discovery: GlobalDiscoveryService
    normalized_base_url: str | None

    def _provider_selection(self) -> DiscoverySelection:
        if self.normalized_base_url is None:
            raise ValueError("The self-hosted Firecrawl provider identity is unavailable.")
        provider_identity = json.dumps(
            {
                "connection_id": None,
                "connection_type": "self_hosted",
                "credential_revision": None,
                "normalized_base_url": self.normalized_base_url.rstrip("/"),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return DiscoverySelection(None, None, None, None, provider_identity)

    def _selection(self, request: DocumentDiscoveryRequest) -> DiscoverySelection:
        if request.firecrawl_connection_id is not None:
            raise ValueError(
                "Workspace Firecrawl connections are not supported in self-hosted mode."
            )
        return self._provider_selection()

    async def prepare(
        self,
        scope: WorkspaceScope,
        request: DocumentDiscoveryRequest,
    ) -> DiscoverySelection:
        del scope
        return self._selection(request)

    async def prepare_for_job(
        self,
        workspace_id: UUID,
        persisted: DiscoverySelection,
    ) -> DiscoverySelection:
        del workspace_id
        if any(
            value is not None
            for value in (
                persisted.connection_id,
                persisted.connection_name,
                persisted.connection_type,
                persisted.credential_revision,
            )
        ):
            raise ValueError("The persisted self-hosted provider selection is invalid.")
        return self._provider_selection()

    async def search_prepared(
        self,
        workspace_id: UUID,
        request: DocumentDiscoveryRequest,
        selection: DiscoverySelection,
        now: datetime,
    ) -> ScopedDiscoveryResult:
        del workspace_id, now
        if selection != self._selection(request):
            raise ValueError("The prepared Firecrawl provider identity has changed.")
        return ScopedDiscoveryResult(
            response=await self.discovery.search(request),
            connection_id=None,
            connection_name_snapshot=None,
            connection_type_snapshot=None,
        )

    async def search(
        self,
        scope: WorkspaceScope,
        request: DocumentDiscoveryRequest,
        now: datetime,
    ) -> ScopedDiscoveryResult:
        selection = await self.prepare(scope, request)
        return await self.search_prepared(scope.workspace_id, request, selection, now)


@dataclass(frozen=True)
class HostedScopedDiscovery:
    connections: ConnectionResolver
    executor: FirecrawlExecutor

    async def prepare(
        self,
        scope: WorkspaceScope,
        request: DocumentDiscoveryRequest,
    ) -> DiscoverySelection:
        return await self.connections.prepare_discovery(scope, request)

    async def prepare_for_job(
        self,
        workspace_id: UUID,
        persisted: DiscoverySelection,
    ) -> DiscoverySelection:
        if persisted.connection_id is None or persisted.credential_revision is None:
            raise ValueError("The persisted hosted provider selection is invalid.")
        return await self.connections.prepare_discovery_for_job(
            workspace_id,
            persisted.connection_id,
            persisted.credential_revision,
        )

    async def search_prepared(
        self,
        workspace_id: UUID,
        request: DocumentDiscoveryRequest,
        selection: DiscoverySelection,
        now: datetime,
    ) -> ScopedDiscoveryResult:
        connection = await self.connections.resolve_connection_for_job(workspace_id, selection)
        return await self._search_with_connection(request, connection, selection, now)

    async def search(
        self,
        scope: WorkspaceScope,
        request: DocumentDiscoveryRequest,
        now: datetime,
    ) -> ScopedDiscoveryResult:
        selection = await self.prepare(scope, request)
        return await self.search_prepared(scope.workspace_id, request, selection, now)

    async def _search_with_connection(
        self,
        request: DocumentDiscoveryRequest,
        connection: ResolvedConnection,
        selection: DiscoverySelection,
        now: datetime,
    ) -> ScopedDiscoveryResult:
        discovery = DiscoveryService(_ResolvedMetadataProvider(self.executor, connection))
        try:
            response = await discovery.search(request)
        except ConnectionExecutionError as error:
            await self.connections.record_execution_failure(connection, error.category, now)
            raise FirecrawlAdapterError(
                code=error.category.value,
                retryable=error.category
                in {
                    ConnectionFailureCategory.TIMEOUT,
                    ConnectionFailureCategory.RATE_LIMITED,
                    ConnectionFailureCategory.SERVICE_UNAVAILABLE,
                },
            ) from None
        return ScopedDiscoveryResult(
            response=response,
            connection_id=selection.connection_id,
            connection_name_snapshot=selection.connection_name,
            connection_type_snapshot=selection.connection_type,
        )
