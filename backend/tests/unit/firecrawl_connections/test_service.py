from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from parserium_collector.adapters.firecrawl.contracts import (
    MetadataSearchRequest,
    MetadataSearchResult,
)
from parserium_collector.features.discovery.models import DocumentDiscoveryRequest
from parserium_collector.features.firecrawl_connections.crypto import (
    CredentialVault,
    FileKeyProvider,
)
from parserium_collector.features.firecrawl_connections.errors import (
    ConnectionNotFoundError,
    ConnectionPermissionError,
    ConnectionUnavailableError,
)
from parserium_collector.features.firecrawl_connections.executor import (
    ConnectionExecutionError,
    FirecrawlExecutor,
)
from parserium_collector.features.firecrawl_connections.models import (
    ConnectionFailureCategory,
    ConnectionStatus,
    ConnectionType,
    ConnectionUpdate,
    CreateConnectionRequest,
    CredentialEnvelope,
    FirecrawlConnectionRecord,
    NewConnection,
    ResolvedConnection,
    RewrappedEnvelope,
    UpdateConnectionRequest,
    ValidationOutcome,
)
from parserium_collector.features.firecrawl_connections.repository import (
    FirecrawlConnectionRepository,
)
from parserium_collector.features.firecrawl_connections.service import (
    FirecrawlConnectionService,
)
from parserium_collector.features.identity.models import WorkspaceRole, WorkspaceScope

NOW = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
WORKSPACE_A = UUID("10000000-0000-4000-8000-000000000016")
WORKSPACE_B = UUID("10000000-0000-4000-8000-000000000026")
USER_A = UUID("20000000-0000-4000-8000-000000000016")
USER_B = UUID("20000000-0000-4000-8000-000000000026")
CONNECTION_A = UUID("30000000-0000-4000-8000-000000000016")
CONNECTION_B = UUID("30000000-0000-4000-8000-000000000026")
OWNER_SCOPE = WorkspaceScope(WORKSPACE_A, USER_A, WorkspaceRole.OWNER)
MEMBER_SCOPE = WorkspaceScope(WORKSPACE_A, USER_B, WorkspaceRole.MEMBER)
OTHER_OWNER_SCOPE = WorkspaceScope(WORKSPACE_B, USER_B, WorkspaceRole.OWNER)


class RecordingVault(CredentialVault):
    def __init__(self) -> None:
        super().__init__(FileKeyProvider.from_bytes(key_id="test-key", key=b"k" * 32))
        self.decrypt_calls = 0

    async def decrypt(
        self,
        envelope: CredentialEnvelope | dict[str, object],
        workspace_id: UUID,
        connection_id: UUID,
        revision: int,
    ) -> str:
        self.decrypt_calls += 1
        return await super().decrypt(envelope, workspace_id, connection_id, revision)


class RecordingExecutor(FirecrawlExecutor):
    def __init__(self) -> None:
        self.validation_failure: ConnectionFailureCategory | None = None
        self.requests: list[MetadataSearchRequest] = []
        self.connection_types: list[ConnectionType] = []
        self.authorization_matched = False

    async def search(
        self,
        connection: ResolvedConnection,
        request: MetadataSearchRequest,
    ) -> MetadataSearchResult:
        self.requests.append(request)
        self.connection_types.append(connection.connection_type)
        self.authorization_matched = connection.credential in {
            "initial-test-token",
            "new-test-token",
        }
        if self.validation_failure is not None:
            raise ConnectionExecutionError(self.validation_failure)
        return MetadataSearchResult(search_id="validation-search", results=[])


class InMemoryConnectionRepository(FirecrawlConnectionRepository):
    def __init__(self) -> None:
        self.records: dict[tuple[UUID, UUID], FirecrawlConnectionRecord] = {}

    async def list_connections(
        self,
        workspace_id: UUID,
    ) -> tuple[FirecrawlConnectionRecord, ...]:
        return tuple(
            sorted(
                (
                    record
                    for (owner, _), record in self.records.items()
                    if owner == workspace_id and record.deleted_at is None
                ),
                key=lambda record: (record.created_at, record.id),
            )
        )

    async def get_connection(
        self,
        workspace_id: UUID,
        connection_id: UUID,
    ) -> FirecrawlConnectionRecord | None:
        record = self.records.get((workspace_id, connection_id))
        return record if record is not None and record.deleted_at is None else None

    async def get_usable_connection_for_job(
        self,
        workspace_id: UUID,
        connection_id: UUID,
        revision: int,
    ) -> FirecrawlConnectionRecord | None:
        record = self.records.get((workspace_id, connection_id))
        if (
            record is None
            or not record.usable
            or record.credential_revision != revision
            or record.credential_envelope is None
        ):
            return None
        return record

    async def create_connection(
        self,
        scope: WorkspaceScope,
        draft: NewConnection,
        now: datetime,
    ) -> FirecrawlConnectionRecord:
        record = FirecrawlConnectionRecord(
            id=draft.id,
            workspace_id=scope.workspace_id,
            name=draft.name,
            normalized_name=draft.normalized_name,
            connection_type=draft.connection_type,
            normalized_base_url=draft.normalized_base_url,
            credential_envelope=draft.credential_envelope,
            credential_revision=draft.credential_revision,
            validated_revision=None,
            validation_succeeded=None,
            capability_profile=None,
            last_validation_attempt_at=None,
            last_validation_success_at=None,
            last_failure_category=None,
            enabled=draft.enabled,
            is_default=draft.is_default,
            created_by_user_id=scope.user_id,
            updated_by_user_id=scope.user_id,
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )
        self.records[(scope.workspace_id, draft.id)] = record
        return record

    async def update_metadata(
        self,
        scope: WorkspaceScope,
        connection_id: UUID,
        update_value: ConnectionUpdate,
        now: datetime,
    ) -> FirecrawlConnectionRecord | None:
        record = await self.get_connection(scope.workspace_id, connection_id)
        if record is None:
            return None
        if update_value.is_default:
            for key, candidate in tuple(self.records.items()):
                if key[0] == scope.workspace_id:
                    self.records[key] = replace(candidate, is_default=False)
        values: dict[str, object] = {
            "updated_by_user_id": scope.user_id,
            "updated_at": now,
        }
        for field_name in ("name", "normalized_name", "enabled", "is_default"):
            value = getattr(update_value, field_name)
            if value is not None:
                values[field_name] = value
        if update_value.enabled is False:
            values["is_default"] = False
        if update_value.endpoint_changed:
            values.update(
                normalized_base_url=update_value.normalized_base_url,
                validated_revision=None,
                validation_succeeded=None,
                capability_profile=None,
                last_validation_attempt_at=None,
                last_validation_success_at=None,
                last_failure_category=None,
            )
        changed = replace(record, **values)
        self.records[(scope.workspace_id, connection_id)] = changed
        return changed

    async def replace_credential(
        self,
        scope: WorkspaceScope,
        connection_id: UUID,
        envelope: CredentialEnvelope,
        expected_revision: int,
        now: datetime,
    ) -> FirecrawlConnectionRecord | None:
        record = await self.get_connection(scope.workspace_id, connection_id)
        if record is None or record.credential_revision != expected_revision:
            return None
        changed = replace(
            record,
            credential_envelope=envelope,
            credential_revision=expected_revision + 1,
            validated_revision=None,
            validation_succeeded=None,
            capability_profile=None,
            last_validation_attempt_at=None,
            last_validation_success_at=None,
            last_failure_category=None,
            updated_at=now,
        )
        self.records[(scope.workspace_id, connection_id)] = changed
        return changed

    async def record_validation(
        self,
        workspace_id: UUID,
        connection_id: UUID,
        revision: int,
        outcome: ValidationOutcome,
        now: datetime,
    ) -> FirecrawlConnectionRecord | None:
        record = await self.get_connection(workspace_id, connection_id)
        if record is None or record.credential_revision != revision:
            return None
        changed = replace(
            record,
            validated_revision=revision if outcome.succeeded else record.validated_revision,
            validation_succeeded=outcome.succeeded,
            capability_profile=outcome.capability_profile,
            last_validation_attempt_at=now,
            last_validation_success_at=(
                now if outcome.succeeded else record.last_validation_success_at
            ),
            last_failure_category=outcome.failure_category,
            updated_at=now,
        )
        self.records[(workspace_id, connection_id)] = changed
        return changed

    async def tombstone(
        self,
        scope: WorkspaceScope,
        connection_id: UUID,
        now: datetime,
    ) -> bool:
        record = await self.get_connection(scope.workspace_id, connection_id)
        if record is None:
            return False
        self.records[(scope.workspace_id, connection_id)] = replace(
            record,
            normalized_base_url=None,
            credential_envelope=None,
            enabled=False,
            is_default=False,
            deleted_at=now,
            updated_at=now,
        )
        return True

    async def list_envelopes_for_rewrap(
        self,
        after_id: UUID | None,
        batch_size: int,
    ) -> tuple[FirecrawlConnectionRecord, ...]:
        return ()

    async def replace_wrapped_keys(
        self,
        replacements: tuple[RewrappedEnvelope, ...],
    ) -> int:
        return 0


def build_service() -> tuple[
    FirecrawlConnectionService,
    InMemoryConnectionRepository,
    RecordingVault,
    RecordingExecutor,
]:
    repository = InMemoryConnectionRepository()
    vault = RecordingVault()
    executor = RecordingExecutor()
    service = FirecrawlConnectionService(
        repository,
        vault,
        executor,
        connection_id_factory=lambda: CONNECTION_A,
        remote_allowed_ports=(443, 9443),
    )
    return service, repository, vault, executor


async def create_cloud(
    service: FirecrawlConnectionService,
    *,
    make_default: bool = True,
) -> FirecrawlConnectionRecord:
    return await service.create_connection(
        OWNER_SCOPE,
        CreateConnectionRequest(
            name="Primary Cloud",
            connection_type="cloud",
            credential="initial-test-token",
            is_default=make_default,
        ),
        NOW,
    )


async def test_owner_creation_validates_before_setting_default() -> None:
    service, _, _, executor = build_service()

    created = await create_cloud(service)

    assert created.status is ConnectionStatus.HEALTHY
    assert created.is_default is True
    assert executor.authorization_matched is True
    assert executor.requests == [
        MetadataSearchRequest(query="site:example.com filetype:pdf", limit=1)
    ]


async def test_failed_validation_retains_a_degraded_non_default_connection() -> None:
    service, repository, _, executor = build_service()
    executor.validation_failure = ConnectionFailureCategory.INVALID_CREDENTIALS

    created = await create_cloud(service)

    assert created.status is ConnectionStatus.DEGRADED
    assert created.usable is False
    assert created.is_default is False
    assert await repository.get_connection(WORKSPACE_A, CONNECTION_A) == created


async def test_member_can_resolve_but_never_manage_or_receive_endpoint() -> None:
    service, _, vault, _ = build_service()
    await create_cloud(service)

    summaries = await service.list_connections(MEMBER_SCOPE)
    resolved = await service.resolve_connection(MEMBER_SCOPE, CONNECTION_A)

    assert summaries.connections[0].usable is True
    assert (
        "normalized_base_url"
        not in summaries.model_dump(mode="json", exclude_none=True)["connections"][0]
    )
    assert resolved.id == CONNECTION_A
    assert vault.decrypt_calls == 2
    with pytest.raises(ConnectionPermissionError):
        await service.delete_connection(MEMBER_SCOPE, CONNECTION_A, NOW)


async def test_cross_workspace_lookup_fails_before_credential_decryption() -> None:
    service, _, vault, _ = build_service()
    await create_cloud(service)
    decrypts_after_create = vault.decrypt_calls

    with pytest.raises(ConnectionNotFoundError):
        await service.resolve_connection(OTHER_OWNER_SCOPE, CONNECTION_A)

    assert vault.decrypt_calls == decrypts_after_create


async def test_discovery_preparation_selects_without_decryption_or_provider_call() -> None:
    service, _, vault, executor = build_service()
    created = await create_cloud(service)
    decrypts = vault.decrypt_calls
    calls = len(executor.requests)

    implicit = await service.prepare_discovery(
        MEMBER_SCOPE, DocumentDiscoveryRequest(query="bank reports")
    )
    explicit = await service.prepare_discovery(
        MEMBER_SCOPE,
        DocumentDiscoveryRequest(query="bank reports", firecrawl_connection_id=created.id),
    )

    assert implicit == explicit
    assert explicit.connection_id == created.id
    assert explicit.credential_revision == created.credential_revision
    assert not hasattr(explicit, "credential")
    assert not hasattr(explicit, "credential_envelope")
    assert "initial-test-token" not in repr(explicit)
    assert vault.decrypt_calls == decrypts
    assert len(executor.requests) == calls


async def test_discovery_preparation_rejects_foreign_workspace_before_decryption() -> None:
    service, _, vault, executor = build_service()
    created = await create_cloud(service)
    decrypts = vault.decrypt_calls
    calls = len(executor.requests)

    with pytest.raises(ConnectionNotFoundError):
        await service.prepare_discovery(
            OTHER_OWNER_SCOPE,
            DocumentDiscoveryRequest(query="reports", firecrawl_connection_id=created.id),
        )
    assert vault.decrypt_calls == decrypts
    assert len(executor.requests) == calls


async def test_worker_preparation_and_resolution_keep_exact_selected_connection() -> None:
    service, repository, vault, executor = build_service()
    created = await create_cloud(service)
    selection = await service.prepare_discovery(
        MEMBER_SCOPE, DocumentDiscoveryRequest(query="reports")
    )
    decrypts = vault.decrypt_calls
    calls = len(executor.requests)
    # A display-name change must not invalidate provider identity.
    repository.records[(WORKSPACE_A, created.id)] = replace(created, name="Renamed")
    prepared = await service.prepare_discovery_for_job(
        WORKSPACE_A, created.id, created.credential_revision
    )
    assert prepared.provider_identity == selection.provider_identity
    assert vault.decrypt_calls == decrypts

    resolved = await service.resolve_connection_for_job(WORKSPACE_A, selection)
    assert resolved.id == created.id
    assert resolved.credential_revision == created.credential_revision
    assert resolved.credential == "initial-test-token"
    assert vault.decrypt_calls == decrypts + 1
    assert len(executor.requests) == calls


async def test_worker_decrypts_checked_snapshot_without_a_second_lookup(monkeypatch) -> None:
    service, repository, _, _ = build_service()
    created = await create_cloud(service)
    selection = await service.prepare_discovery(
        MEMBER_SCOPE, DocumentDiscoveryRequest(query="reports")
    )
    lookups = 0

    # Test-only race double: a second lookup would observe a different endpoint.
    async def changing_lookup(workspace_id: UUID, connection_id: UUID, revision: int):
        nonlocal lookups
        assert (workspace_id, connection_id) == (WORKSPACE_A, created.id)
        assert revision == created.credential_revision
        lookups += 1
        if lookups == 1:
            return created
        return replace(
            created,
            connection_type=ConnectionType.REMOTE,
            normalized_base_url="https://replacement.example",
        )

    monkeypatch.setattr(repository, "get_usable_connection_for_job", changing_lookup)

    resolved = await service.resolve_connection_for_job(WORKSPACE_A, selection)

    assert lookups == 1
    assert resolved.connection_type is ConnectionType.CLOUD
    assert resolved.normalized_base_url is None
    assert resolved.credential_revision == created.credential_revision


@pytest.mark.parametrize(
    "changes",
    [
        {"enabled": False},
        {"deleted_at": NOW},
        {"validation_succeeded": None},
        {"validation_succeeded": False},
        {"validated_revision": None},
        {"credential_revision": 2, "validated_revision": 2},
        {"credential_envelope": None},
    ],
)
async def test_worker_rejects_unavailable_selection_before_decryption(changes) -> None:
    service, repository, vault, executor = build_service()
    created = await create_cloud(service)
    selection = await service.prepare_discovery(
        MEMBER_SCOPE, DocumentDiscoveryRequest(query="reports")
    )
    decrypts = vault.decrypt_calls
    calls = len(executor.requests)
    repository.records[(WORKSPACE_A, created.id)] = replace(created, **changes)

    with pytest.raises(ConnectionUnavailableError):
        await service.prepare_discovery_for_job(
            WORKSPACE_A, created.id, created.credential_revision
        )
    with pytest.raises(ConnectionUnavailableError):
        await service.resolve_connection_for_job(WORKSPACE_A, selection)
    assert vault.decrypt_calls == decrypts
    assert len(executor.requests) == calls


async def test_worker_foreign_or_missing_selection_is_uniformly_unavailable() -> None:
    service, _, vault, _ = build_service()
    created = await create_cloud(service)
    selection = await service.prepare_discovery(
        MEMBER_SCOPE, DocumentDiscoveryRequest(query="reports")
    )
    decrypts = vault.decrypt_calls
    for workspace, selected in (
        (WORKSPACE_B, selection),
        (WORKSPACE_A, replace(selection, connection_id=CONNECTION_B)),
        (WORKSPACE_A, replace(selection, connection_id=None)),
        (WORKSPACE_A, replace(selection, credential_revision=None)),
    ):
        with pytest.raises(ConnectionUnavailableError):
            await service.resolve_connection_for_job(workspace, selected)
    with pytest.raises(ConnectionUnavailableError):
        await service.prepare_discovery_for_job(
            WORKSPACE_B, created.id, created.credential_revision
        )
    assert vault.decrypt_calls == decrypts


async def test_worker_rejects_changed_endpoint_even_when_revision_is_unchanged() -> None:
    service, repository, vault, executor = build_service()
    created = await service.create_connection(
        OWNER_SCOPE,
        CreateConnectionRequest(
            name="Remote",
            connection_type="remote",
            base_url="https://crawl.example",
            credential="initial-test-token",
            is_default=True,
        ),
        NOW,
    )
    selection = await service.prepare_discovery(
        MEMBER_SCOPE, DocumentDiscoveryRequest(query="reports")
    )
    repository.records[(WORKSPACE_A, created.id)] = replace(
        created, normalized_base_url="https://replacement.example"
    )
    decrypts = vault.decrypt_calls
    calls = len(executor.requests)
    current = await service.prepare_discovery_for_job(
        WORKSPACE_A, created.id, created.credential_revision
    )
    assert current.provider_identity != selection.provider_identity
    assert current.credential_revision == selection.credential_revision
    with pytest.raises(ConnectionUnavailableError):
        await service.resolve_connection_for_job(WORKSPACE_A, selection)
    assert vault.decrypt_calls == decrypts
    assert len(executor.requests) == calls


async def test_rotation_is_unusable_until_current_revision_validates() -> None:
    service, _, _, executor = build_service()
    await create_cloud(service)
    executor.validation_failure = ConnectionFailureCategory.INVALID_CREDENTIALS

    failed = await service.replace_credential(
        OWNER_SCOPE,
        CONNECTION_A,
        "new-test-token",
        NOW + timedelta(seconds=1),
    )

    assert failed.credential_revision == 2
    assert failed.status is ConnectionStatus.DEGRADED
    assert failed.usable is False

    executor.validation_failure = None
    healthy = await service.test_connection(
        OWNER_SCOPE,
        CONNECTION_A,
        NOW + timedelta(seconds=2),
    )
    assert healthy.credential_revision == 2
    assert healthy.status is ConnectionStatus.HEALTHY
    assert healthy.usable is True


async def test_remote_endpoint_change_normalizes_and_revalidates() -> None:
    service, _, _, executor = build_service()
    created = await service.create_connection(
        OWNER_SCOPE,
        CreateConnectionRequest(
            name="Remote",
            connection_type="remote",
            base_url="https://Firecrawl.Example.:9443/",
            credential="initial-test-token",
        ),
        NOW,
    )

    changed = await service.update_connection(
        OWNER_SCOPE,
        created.id,
        UpdateConnectionRequest(base_url="https://Other.Example:9443"),
        NOW + timedelta(seconds=1),
    )

    assert changed.normalized_base_url == "https://other.example:9443"
    assert changed.status is ConnectionStatus.HEALTHY
    assert executor.connection_types == [ConnectionType.REMOTE, ConnectionType.REMOTE]


async def test_disable_blocks_resolution_and_delete_allows_healthy_fallback() -> None:
    service, repository, _, _ = build_service()
    first = await create_cloud(service)
    second_envelope = await RecordingVault().encrypt(
        "initial-test-token",
        WORKSPACE_A,
        CONNECTION_B,
        1,
    )
    second = await repository.create_connection(
        OWNER_SCOPE,
        NewConnection(
            id=CONNECTION_B,
            name="Fallback",
            normalized_name="fallback",
            connection_type=ConnectionType.CLOUD,
            normalized_base_url=None,
            credential_envelope=second_envelope,
            credential_revision=1,
            enabled=True,
            is_default=False,
        ),
        NOW + timedelta(seconds=1),
    )
    second = await repository.record_validation(
        WORKSPACE_A,
        CONNECTION_B,
        1,
        ValidationOutcome(True, {"contract": "metadata-search-v2"}, None),
        NOW + timedelta(seconds=1),
    )
    assert second is not None

    disabled = await service.update_connection(
        OWNER_SCOPE,
        first.id,
        UpdateConnectionRequest(enabled=False),
        NOW + timedelta(seconds=2),
    )
    assert disabled.status is ConnectionStatus.DISABLED
    with pytest.raises(ConnectionUnavailableError):
        await service.resolve_connection(OWNER_SCOPE, first.id)

    await service.delete_connection(
        OWNER_SCOPE,
        first.id,
        NOW + timedelta(seconds=3),
    )
    fallback = await service.resolve_connection(OWNER_SCOPE, None)
    assert fallback.id == CONNECTION_B


@pytest.mark.parametrize(
    ("changes", "expected_error"),
    [
        ({"validation_succeeded": False}, ConnectionUnavailableError),
        ({"enabled": False}, ConnectionUnavailableError),
        (
            {"credential_revision": 2, "validated_revision": 1},
            ConnectionUnavailableError,
        ),
        ({"deleted_at": NOW}, ConnectionNotFoundError),
    ],
)
async def test_unusable_selected_connections_are_rejected_before_decryption(
    changes: dict[str, object],
    expected_error: type[RuntimeError],
) -> None:
    service, repository, vault, _ = build_service()
    await create_cloud(service)
    decrypts_after_create = vault.decrypt_calls
    repository.records[(WORKSPACE_A, CONNECTION_A)] = replace(
        repository.records[(WORKSPACE_A, CONNECTION_A)],
        **changes,
    )

    with pytest.raises(expected_error):
        await service.resolve_connection(OWNER_SCOPE, CONNECTION_A)

    assert vault.decrypt_calls == decrypts_after_create


async def test_execution_failure_marks_only_the_matching_revision_degraded() -> None:
    service, repository, _, _ = build_service()
    connection = await create_cloud(service)
    resolved = await service.resolve_connection(OWNER_SCOPE, connection.id)

    await service.record_execution_failure(
        resolved,
        ConnectionFailureCategory.TIMEOUT,
        NOW + timedelta(seconds=1),
    )

    changed = await repository.get_connection(WORKSPACE_A, CONNECTION_A)
    assert changed is not None
    assert changed.status is ConnectionStatus.DEGRADED
    assert changed.last_failure_category is ConnectionFailureCategory.TIMEOUT
