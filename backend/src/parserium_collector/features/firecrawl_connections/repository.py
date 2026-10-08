from datetime import datetime
from typing import Protocol
from uuid import UUID

from sqlalchemy import RowMapping, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from parserium_collector.adapters.database.tables import firecrawl_connections
from parserium_collector.features.firecrawl_connections.errors import (
    ConnectionDefaultConflictError,
    ConnectionNameConflictError,
    CredentialUnavailableError,
)
from parserium_collector.features.firecrawl_connections.models import (
    ConnectionFailureCategory,
    ConnectionType,
    ConnectionUpdate,
    CredentialEnvelope,
    FirecrawlConnectionRecord,
    NewConnection,
    RewrappedEnvelope,
    ValidationOutcome,
)
from parserium_collector.features.identity.models import WorkspaceScope

_NAME_CONSTRAINT = "uq_firecrawl_connections_workspace_name_live"
_DEFAULT_CONSTRAINT = "uq_firecrawl_connections_workspace_default_live"


def _record(row: RowMapping) -> FirecrawlConnectionRecord:
    envelope = (
        CredentialEnvelope.model_validate(row.credential_envelope)
        if row.credential_envelope is not None
        else None
    )
    return FirecrawlConnectionRecord(
        id=row.id,
        workspace_id=row.workspace_id,
        name=row.name,
        normalized_name=row.normalized_name,
        connection_type=ConnectionType(row.connection_type),
        normalized_base_url=row.normalized_base_url,
        credential_envelope=envelope,
        credential_revision=row.credential_revision,
        validated_revision=row.validated_revision,
        validation_succeeded=row.validation_succeeded,
        capability_profile=row.capability_profile,
        last_validation_attempt_at=row.last_validation_attempt_at,
        last_validation_success_at=row.last_validation_success_at,
        last_failure_category=(
            ConnectionFailureCategory(row.last_failure_category)
            if row.last_failure_category is not None
            else None
        ),
        enabled=row.enabled,
        is_default=row.is_default,
        created_by_user_id=row.created_by_user_id,
        updated_by_user_id=row.updated_by_user_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
        deleted_at=row.deleted_at,
    )


def _constraint_name(error: IntegrityError) -> str | None:
    diagnostic = getattr(error.orig, "diag", None)
    return getattr(diagnostic, "constraint_name", None)


def _map_integrity_error(error: IntegrityError) -> None:
    constraint_name = _constraint_name(error)
    if constraint_name == _NAME_CONSTRAINT:
        raise ConnectionNameConflictError from None
    if constraint_name == _DEFAULT_CONSTRAINT:
        raise ConnectionDefaultConflictError from None
    raise error


class FirecrawlConnectionRepository(Protocol):
    async def list_connections(
        self,
        workspace_id: UUID,
    ) -> tuple[FirecrawlConnectionRecord, ...]: ...

    async def get_connection(
        self,
        workspace_id: UUID,
        connection_id: UUID,
    ) -> FirecrawlConnectionRecord | None: ...

    async def get_usable_connection_for_job(
        self,
        workspace_id: UUID,
        connection_id: UUID,
        revision: int,
    ) -> FirecrawlConnectionRecord | None: ...

    async def create_connection(
        self,
        scope: WorkspaceScope,
        draft: NewConnection,
        now: datetime,
    ) -> FirecrawlConnectionRecord: ...

    async def update_metadata(
        self,
        scope: WorkspaceScope,
        connection_id: UUID,
        update_value: ConnectionUpdate,
        now: datetime,
    ) -> FirecrawlConnectionRecord | None: ...

    async def replace_credential(
        self,
        scope: WorkspaceScope,
        connection_id: UUID,
        envelope: CredentialEnvelope,
        expected_revision: int,
        now: datetime,
    ) -> FirecrawlConnectionRecord | None: ...

    async def record_validation(
        self,
        workspace_id: UUID,
        connection_id: UUID,
        revision: int,
        outcome: ValidationOutcome,
        now: datetime,
    ) -> FirecrawlConnectionRecord | None: ...

    async def tombstone(
        self,
        scope: WorkspaceScope,
        connection_id: UUID,
        now: datetime,
    ) -> bool: ...

    async def list_envelopes_for_rewrap(
        self,
        after_id: UUID | None,
        batch_size: int,
    ) -> tuple[FirecrawlConnectionRecord, ...]: ...

    async def replace_wrapped_keys(
        self,
        replacements: tuple[RewrappedEnvelope, ...],
    ) -> int: ...


class PostgresFirecrawlConnectionRepository:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def list_connections(
        self,
        workspace_id: UUID,
    ) -> tuple[FirecrawlConnectionRecord, ...]:
        statement = (
            select(firecrawl_connections)
            .where(
                firecrawl_connections.c.workspace_id == workspace_id,
                firecrawl_connections.c.deleted_at.is_(None),
            )
            .order_by(firecrawl_connections.c.created_at, firecrawl_connections.c.id)
        )
        async with self._engine.connect() as connection:
            rows = (await connection.execute(statement)).mappings().all()
        return tuple(_record(row) for row in rows)

    async def get_connection(
        self,
        workspace_id: UUID,
        connection_id: UUID,
    ) -> FirecrawlConnectionRecord | None:
        statement = select(firecrawl_connections).where(
            firecrawl_connections.c.workspace_id == workspace_id,
            firecrawl_connections.c.id == connection_id,
            firecrawl_connections.c.deleted_at.is_(None),
        )
        async with self._engine.connect() as connection:
            row = (await connection.execute(statement)).mappings().one_or_none()
        return _record(row) if row is not None else None

    async def get_usable_connection_for_job(
        self,
        workspace_id: UUID,
        connection_id: UUID,
        revision: int,
    ) -> FirecrawlConnectionRecord | None:
        statement = select(firecrawl_connections).where(
            firecrawl_connections.c.workspace_id == workspace_id,
            firecrawl_connections.c.id == connection_id,
            firecrawl_connections.c.deleted_at.is_(None),
            firecrawl_connections.c.enabled.is_(True),
            firecrawl_connections.c.credential_envelope.is_not(None),
            firecrawl_connections.c.credential_revision == revision,
            firecrawl_connections.c.validated_revision == revision,
            firecrawl_connections.c.validation_succeeded.is_(True),
        )
        async with self._engine.connect() as connection:
            row = (await connection.execute(statement)).mappings().one_or_none()
        return _record(row) if row is not None else None

    async def create_connection(
        self,
        scope: WorkspaceScope,
        draft: NewConnection,
        now: datetime,
    ) -> FirecrawlConnectionRecord:
        statement = (
            insert(firecrawl_connections)
            .values(
                id=draft.id,
                workspace_id=scope.workspace_id,
                name=draft.name,
                normalized_name=draft.normalized_name,
                connection_type=draft.connection_type.value,
                normalized_base_url=draft.normalized_base_url,
                credential_envelope=draft.credential_envelope.model_dump(mode="json"),
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
            .returning(*firecrawl_connections.c)
        )
        try:
            async with self._engine.begin() as connection:
                row = (await connection.execute(statement)).mappings().one()
        except IntegrityError as error:
            _map_integrity_error(error)
            raise AssertionError("unreachable") from error
        return _record(row)

    async def update_metadata(
        self,
        scope: WorkspaceScope,
        connection_id: UUID,
        update_value: ConnectionUpdate,
        now: datetime,
    ) -> FirecrawlConnectionRecord | None:
        try:
            async with self._engine.begin() as connection:
                if update_value.is_default is True:
                    locked_rows = (
                        (
                            await connection.execute(
                                select(firecrawl_connections)
                                .where(
                                    firecrawl_connections.c.workspace_id == scope.workspace_id,
                                    firecrawl_connections.c.deleted_at.is_(None),
                                )
                                .order_by(firecrawl_connections.c.id)
                                .with_for_update()
                            )
                        )
                        .mappings()
                        .all()
                    )
                    target = next(
                        (row for row in locked_rows if row.id == connection_id),
                        None,
                    )
                else:
                    target = (
                        (
                            await connection.execute(
                                select(firecrawl_connections)
                                .where(
                                    firecrawl_connections.c.workspace_id == scope.workspace_id,
                                    firecrawl_connections.c.id == connection_id,
                                    firecrawl_connections.c.deleted_at.is_(None),
                                )
                                .with_for_update()
                            )
                        )
                        .mappings()
                        .one_or_none()
                    )
                if target is None:
                    return None

                values: dict[str, object] = {
                    "updated_by_user_id": scope.user_id,
                    "updated_at": now,
                }
                if update_value.name is not None:
                    values["name"] = update_value.name
                if update_value.normalized_name is not None:
                    values["normalized_name"] = update_value.normalized_name
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
                if update_value.enabled is not None:
                    values["enabled"] = update_value.enabled
                    if not update_value.enabled:
                        values["is_default"] = False
                if update_value.is_default is not None:
                    values["is_default"] = update_value.is_default

                if update_value.is_default is True:
                    target_record = _record(target)
                    target_enabled = (
                        update_value.enabled
                        if update_value.enabled is not None
                        else target_record.enabled
                    )
                    if (
                        not target_enabled
                        or update_value.endpoint_changed
                        or not target_record.usable
                    ):
                        raise ConnectionDefaultConflictError
                    await connection.execute(
                        update(firecrawl_connections)
                        .where(
                            firecrawl_connections.c.workspace_id == scope.workspace_id,
                            firecrawl_connections.c.id != connection_id,
                            firecrawl_connections.c.deleted_at.is_(None),
                            firecrawl_connections.c.is_default.is_(True),
                        )
                        .values(
                            is_default=False,
                            updated_by_user_id=scope.user_id,
                            updated_at=now,
                        )
                    )

                changed = (
                    (
                        await connection.execute(
                            update(firecrawl_connections)
                            .where(
                                firecrawl_connections.c.workspace_id == scope.workspace_id,
                                firecrawl_connections.c.id == connection_id,
                                firecrawl_connections.c.deleted_at.is_(None),
                            )
                            .values(**values)
                            .returning(*firecrawl_connections.c)
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                return _record(changed) if changed is not None else None
        except IntegrityError as error:
            _map_integrity_error(error)
            raise AssertionError("unreachable") from error

    async def replace_credential(
        self,
        scope: WorkspaceScope,
        connection_id: UUID,
        envelope: CredentialEnvelope,
        expected_revision: int,
        now: datetime,
    ) -> FirecrawlConnectionRecord | None:
        statement = (
            update(firecrawl_connections)
            .where(
                firecrawl_connections.c.workspace_id == scope.workspace_id,
                firecrawl_connections.c.id == connection_id,
                firecrawl_connections.c.deleted_at.is_(None),
                firecrawl_connections.c.credential_revision == expected_revision,
            )
            .values(
                credential_envelope=envelope.model_dump(mode="json"),
                credential_revision=expected_revision + 1,
                validated_revision=None,
                validation_succeeded=None,
                capability_profile=None,
                last_validation_attempt_at=None,
                last_validation_success_at=None,
                last_failure_category=None,
                updated_by_user_id=scope.user_id,
                updated_at=now,
            )
            .returning(*firecrawl_connections.c)
        )
        async with self._engine.begin() as connection:
            row = (await connection.execute(statement)).mappings().one_or_none()
        return _record(row) if row is not None else None

    async def record_validation(
        self,
        workspace_id: UUID,
        connection_id: UUID,
        revision: int,
        outcome: ValidationOutcome,
        now: datetime,
    ) -> FirecrawlConnectionRecord | None:
        values: dict[str, object] = {
            "validation_succeeded": outcome.succeeded,
            "capability_profile": outcome.capability_profile if outcome.succeeded else None,
            "last_validation_attempt_at": now,
            "last_failure_category": (
                outcome.failure_category.value if outcome.failure_category is not None else None
            ),
            "updated_at": now,
        }
        if outcome.succeeded:
            values.update(
                validated_revision=revision,
                last_validation_success_at=now,
                last_failure_category=None,
            )
        statement = (
            update(firecrawl_connections)
            .where(
                firecrawl_connections.c.workspace_id == workspace_id,
                firecrawl_connections.c.id == connection_id,
                firecrawl_connections.c.deleted_at.is_(None),
                firecrawl_connections.c.credential_revision == revision,
            )
            .values(**values)
            .returning(*firecrawl_connections.c)
        )
        async with self._engine.begin() as connection:
            row = (await connection.execute(statement)).mappings().one_or_none()
        return _record(row) if row is not None else None

    async def tombstone(
        self,
        scope: WorkspaceScope,
        connection_id: UUID,
        now: datetime,
    ) -> bool:
        statement = (
            update(firecrawl_connections)
            .where(
                firecrawl_connections.c.workspace_id == scope.workspace_id,
                firecrawl_connections.c.id == connection_id,
                firecrawl_connections.c.deleted_at.is_(None),
            )
            .values(
                normalized_base_url=None,
                credential_envelope=None,
                validated_revision=None,
                validation_succeeded=None,
                capability_profile=None,
                last_validation_attempt_at=None,
                last_validation_success_at=None,
                last_failure_category=None,
                enabled=False,
                is_default=False,
                updated_by_user_id=scope.user_id,
                updated_at=now,
                deleted_at=now,
            )
            .returning(firecrawl_connections.c.id)
        )
        async with self._engine.begin() as connection:
            return (await connection.execute(statement)).scalar_one_or_none() is not None

    async def list_envelopes_for_rewrap(
        self,
        after_id: UUID | None,
        batch_size: int,
    ) -> tuple[FirecrawlConnectionRecord, ...]:
        if not 1 <= batch_size <= 1000:
            raise ValueError("The credential rewrap batch size must be from 1 through 1000.")
        statement = (
            select(firecrawl_connections)
            .where(
                firecrawl_connections.c.deleted_at.is_(None),
                firecrawl_connections.c.credential_envelope.is_not(None),
            )
            .order_by(firecrawl_connections.c.id)
            .limit(batch_size)
        )
        if after_id is not None:
            statement = statement.where(firecrawl_connections.c.id > after_id)
        async with self._engine.connect() as connection:
            rows = (await connection.execute(statement)).mappings().all()
        return tuple(_record(row) for row in rows)

    async def replace_wrapped_keys(
        self,
        replacements: tuple[RewrappedEnvelope, ...],
    ) -> int:
        replaced = 0
        async with self._engine.begin() as connection:
            for replacement in replacements:
                row = (
                    (
                        await connection.execute(
                            select(firecrawl_connections)
                            .where(
                                firecrawl_connections.c.workspace_id == replacement.workspace_id,
                                firecrawl_connections.c.id == replacement.connection_id,
                                firecrawl_connections.c.deleted_at.is_(None),
                            )
                            .with_for_update()
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if row is None:
                    raise CredentialUnavailableError
                current = CredentialEnvelope.model_validate(row.credential_envelope)
                if current.key_id != replacement.expected_key_id:
                    raise CredentialUnavailableError
                changed = await connection.execute(
                    update(firecrawl_connections)
                    .where(
                        firecrawl_connections.c.workspace_id == replacement.workspace_id,
                        firecrawl_connections.c.id == replacement.connection_id,
                        firecrawl_connections.c.deleted_at.is_(None),
                    )
                    .values(credential_envelope=replacement.envelope.model_dump(mode="json"))
                    .returning(firecrawl_connections.c.id)
                )
                if changed.scalar_one_or_none() is None:
                    raise CredentialUnavailableError
                replaced += 1
        return replaced
