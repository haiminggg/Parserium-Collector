import json
from collections.abc import Callable
from datetime import datetime
from uuid import UUID, uuid4

from parserium_collector.adapters.firecrawl.contracts import MetadataSearchRequest
from parserium_collector.features.analysis.models import DiscoverySelection
from parserium_collector.features.discovery.models import DocumentDiscoveryRequest
from parserium_collector.features.firecrawl_connections.crypto import CredentialVault
from parserium_collector.features.firecrawl_connections.endpoints import (
    normalize_remote_origin,
)
from parserium_collector.features.firecrawl_connections.errors import (
    ConnectionNotFoundError,
    ConnectionPermissionError,
    ConnectionUnavailableError,
    CredentialUnavailableError,
)
from parserium_collector.features.firecrawl_connections.executor import (
    ConnectionExecutionError,
    FirecrawlExecutor,
)
from parserium_collector.features.firecrawl_connections.models import (
    ConnectionFailureCategory,
    ConnectionListResponse,
    ConnectionSummary,
    ConnectionType,
    ConnectionUpdate,
    CreateConnectionRequest,
    FirecrawlConnectionRecord,
    NewConnection,
    ResolvedConnection,
    UpdateConnectionRequest,
    ValidationOutcome,
    normalize_connection_name,
    validate_credential,
)
from parserium_collector.features.firecrawl_connections.repository import (
    FirecrawlConnectionRepository,
)
from parserium_collector.features.identity.models import (
    WorkspaceRole,
    WorkspaceScope,
)

_VALIDATION_REQUEST = MetadataSearchRequest(
    query="site:example.com filetype:pdf",
    limit=1,
)
_CAPABILITY_PROFILE: dict[str, object] = {
    "contract": "metadata-search-v2",
    "source": "web",
    "fetched_content": False,
}


def require_owner(scope: WorkspaceScope) -> None:
    if scope.role is not WorkspaceRole.OWNER or scope.user_id is None:
        raise ConnectionPermissionError


class FirecrawlConnectionService:
    def __init__(
        self,
        repository: FirecrawlConnectionRepository,
        vault: CredentialVault,
        executor: FirecrawlExecutor,
        *,
        connection_id_factory: Callable[[], UUID] = uuid4,
        remote_allowed_ports: tuple[int, ...] = (443,),
    ) -> None:
        self._repository = repository
        self._vault = vault
        self._executor = executor
        self._connection_id_factory = connection_id_factory
        self._remote_allowed_ports = remote_allowed_ports

    async def list_connections(self, scope: WorkspaceScope) -> ConnectionListResponse:
        records = await self._repository.list_connections(scope.workspace_id)
        include_endpoint = scope.role is WorkspaceRole.OWNER and scope.user_id is not None
        return ConnectionListResponse(
            connections=tuple(
                ConnectionSummary.from_record(
                    record,
                    include_endpoint=include_endpoint,
                )
                for record in records
            )
        )

    async def create_connection(
        self,
        scope: WorkspaceScope,
        request: CreateConnectionRequest,
        now: datetime,
    ) -> FirecrawlConnectionRecord:
        require_owner(scope)
        connection_id = self._connection_id_factory()
        normalized_base_url = self._request_origin(request)
        envelope = await self._vault.encrypt(
            request.credential,
            scope.workspace_id,
            connection_id,
            1,
        )
        created = await self._repository.create_connection(
            scope,
            NewConnection(
                id=connection_id,
                name=request.name,
                normalized_name=normalize_connection_name(request.name),
                connection_type=request.connection_type,
                normalized_base_url=normalized_base_url,
                credential_envelope=envelope,
                credential_revision=1,
                enabled=True,
                is_default=False,
            ),
            now,
        )
        validated = await self._validate(created, now)
        if request.is_default and validated.usable:
            selected = await self._repository.update_metadata(
                scope,
                validated.id,
                ConnectionUpdate(is_default=True),
                now,
            )
            if selected is None:
                raise ConnectionNotFoundError
            return selected
        return validated

    async def update_connection(
        self,
        scope: WorkspaceScope,
        connection_id: UUID,
        request: UpdateConnectionRequest,
        now: datetime,
    ) -> FirecrawlConnectionRecord:
        require_owner(scope)
        current = await self._required_connection(scope.workspace_id, connection_id)
        endpoint_changed = False
        normalized_base_url: str | None = None
        if "base_url" in request.model_fields_set:
            if current.connection_type is ConnectionType.CLOUD:
                raise ValueError("Cloud Firecrawl connections do not accept a custom endpoint.")
            if request.base_url is None:
                raise ValueError("Remote Firecrawl connections require an endpoint.")
            normalized_base_url = normalize_remote_origin(
                request.base_url,
                allowed_ports=self._remote_allowed_ports,
            )
            endpoint_changed = normalized_base_url != current.normalized_base_url

        requested_default = request.is_default
        initial_default = None if endpoint_changed and requested_default else requested_default
        changed = await self._repository.update_metadata(
            scope,
            connection_id,
            ConnectionUpdate(
                name=request.name,
                normalized_name=(
                    normalize_connection_name(request.name) if request.name is not None else None
                ),
                normalized_base_url=normalized_base_url,
                endpoint_changed=endpoint_changed,
                enabled=request.enabled,
                is_default=initial_default,
            ),
            now,
        )
        if changed is None:
            raise ConnectionNotFoundError
        if endpoint_changed:
            changed = await self._validate(changed, now)
        if endpoint_changed and requested_default and changed.usable:
            selected = await self._repository.update_metadata(
                scope,
                connection_id,
                ConnectionUpdate(is_default=True),
                now,
            )
            if selected is None:
                raise ConnectionNotFoundError
            changed = selected
        return changed

    async def replace_credential(
        self,
        scope: WorkspaceScope,
        connection_id: UUID,
        credential: str,
        now: datetime,
    ) -> FirecrawlConnectionRecord:
        require_owner(scope)
        validate_credential(credential)
        current = await self._required_connection(scope.workspace_id, connection_id)
        next_revision = current.credential_revision + 1
        envelope = await self._vault.encrypt(
            credential,
            scope.workspace_id,
            connection_id,
            next_revision,
        )
        changed = await self._repository.replace_credential(
            scope,
            connection_id,
            envelope,
            current.credential_revision,
            now,
        )
        if changed is None:
            raise ConnectionNotFoundError
        return await self._validate(changed, now)

    async def test_connection(
        self,
        scope: WorkspaceScope,
        connection_id: UUID,
        now: datetime,
    ) -> FirecrawlConnectionRecord:
        require_owner(scope)
        current = await self._required_connection(scope.workspace_id, connection_id)
        return await self._validate(current, now)

    async def delete_connection(
        self,
        scope: WorkspaceScope,
        connection_id: UUID,
        now: datetime,
    ) -> None:
        require_owner(scope)
        if not await self._repository.tombstone(scope, connection_id, now):
            raise ConnectionNotFoundError

    async def resolve_connection(
        self,
        scope: WorkspaceScope,
        connection_id: UUID | None,
    ) -> ResolvedConnection:
        record = await self._select_connection(scope.workspace_id, connection_id)
        return await self._resolve(record)

    async def prepare_discovery(
        self,
        scope: WorkspaceScope,
        request: DocumentDiscoveryRequest,
    ) -> DiscoverySelection:
        """Select safe provider identity without decrypting or contacting Firecrawl."""
        record = await self._select_connection(scope.workspace_id, request.firecrawl_connection_id)
        return self._discovery_selection(record)

    async def prepare_discovery_for_job(
        self,
        workspace_id: UUID,
        connection_id: UUID,
        revision: int,
    ) -> DiscoverySelection:
        """Load current identity for the worker's pre-dispatch fingerprint check."""
        record = await self._required_job_connection(workspace_id, connection_id, revision)
        return self._discovery_selection(record)

    async def resolve_connection_for_job(
        self,
        workspace_id: UUID,
        selection: DiscoverySelection,
    ) -> ResolvedConnection:
        """Decrypt only the exact provider configuration selected for this job."""
        if selection.connection_id is None or selection.credential_revision is None:
            raise ConnectionUnavailableError
        record = await self._required_job_connection(
            workspace_id, selection.connection_id, selection.credential_revision
        )
        current = self._discovery_selection(record)
        if (
            current.provider_identity != selection.provider_identity
            or current.connection_type != selection.connection_type
        ):
            raise ConnectionUnavailableError
        # Resolve this immutable snapshot, not a second lookup which could switch endpoints.
        return await self._resolve(record)

    async def _required_job_connection(
        self,
        workspace_id: UUID,
        connection_id: UUID,
        revision: int,
    ) -> FirecrawlConnectionRecord:
        record = await self._repository.get_usable_connection_for_job(
            workspace_id,
            connection_id,
            revision,
        )
        if record is None:
            raise ConnectionUnavailableError
        return record

    @staticmethod
    def _discovery_selection(record: FirecrawlConnectionRecord) -> DiscoverySelection:
        provider_identity = json.dumps(
            {
                "connection_id": str(record.id),
                "connection_type": record.connection_type.value,
                "credential_revision": record.credential_revision,
                "normalized_base_url": record.normalized_base_url,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return DiscoverySelection(
            connection_id=record.id,
            connection_name=record.name,
            connection_type=record.connection_type,
            credential_revision=record.credential_revision,
            provider_identity=provider_identity,
        )

    async def _select_connection(
        self,
        workspace_id: UUID,
        connection_id: UUID | None,
    ) -> FirecrawlConnectionRecord:
        if connection_id is not None:
            record = await self._repository.get_connection(
                workspace_id,
                connection_id,
            )
            if record is None:
                raise ConnectionNotFoundError
            if not record.usable:
                raise ConnectionUnavailableError
        else:
            records = await self._repository.list_connections(workspace_id)
            record = next(
                (candidate for candidate in records if candidate.is_default and candidate.usable),
                None,
            )
            if record is None:
                record = next((candidate for candidate in records if candidate.usable), None)
            if record is None:
                raise ConnectionUnavailableError
        return record

    async def record_execution_failure(
        self,
        connection: ResolvedConnection,
        category: ConnectionFailureCategory,
        now: datetime,
    ) -> None:
        await self._repository.record_validation(
            connection.workspace_id,
            connection.id,
            connection.credential_revision,
            ValidationOutcome(
                succeeded=False,
                capability_profile=None,
                failure_category=category,
            ),
            now,
        )

    def _request_origin(self, request: CreateConnectionRequest) -> str | None:
        if request.connection_type is ConnectionType.CLOUD:
            return None
        if request.base_url is None:
            raise ValueError("Remote Firecrawl connections require an endpoint.")
        return normalize_remote_origin(
            request.base_url,
            allowed_ports=self._remote_allowed_ports,
        )

    async def _required_connection(
        self,
        workspace_id: UUID,
        connection_id: UUID,
    ) -> FirecrawlConnectionRecord:
        record = await self._repository.get_connection(workspace_id, connection_id)
        if record is None:
            raise ConnectionNotFoundError
        return record

    async def _resolve(
        self,
        record: FirecrawlConnectionRecord,
    ) -> ResolvedConnection:
        if record.credential_envelope is None:
            raise CredentialUnavailableError
        credential = await self._vault.decrypt(
            record.credential_envelope,
            record.workspace_id,
            record.id,
            record.credential_revision,
        )
        return ResolvedConnection(
            id=record.id,
            workspace_id=record.workspace_id,
            name=record.name,
            connection_type=record.connection_type,
            normalized_base_url=record.normalized_base_url,
            credential=credential,
            credential_revision=record.credential_revision,
        )

    async def _validate(
        self,
        record: FirecrawlConnectionRecord,
        now: datetime,
    ) -> FirecrawlConnectionRecord:
        try:
            resolved = await self._resolve(record)
            await self._executor.search(resolved, _VALIDATION_REQUEST)
            outcome = ValidationOutcome(
                succeeded=True,
                capability_profile=dict(_CAPABILITY_PROFILE),
                failure_category=None,
            )
        except CredentialUnavailableError:
            outcome = ValidationOutcome(
                succeeded=False,
                capability_profile=None,
                failure_category=ConnectionFailureCategory.CREDENTIAL_UNAVAILABLE,
            )
        except ConnectionExecutionError as error:
            outcome = ValidationOutcome(
                succeeded=False,
                capability_profile=None,
                failure_category=error.category,
            )
        validated = await self._repository.record_validation(
            record.workspace_id,
            record.id,
            record.credential_revision,
            outcome,
            now,
        )
        if validated is not None:
            return validated
        latest = await self._repository.get_connection(record.workspace_id, record.id)
        if latest is None:
            raise ConnectionNotFoundError
        return latest
