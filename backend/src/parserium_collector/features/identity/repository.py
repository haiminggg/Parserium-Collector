from datetime import datetime
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy import RowMapping, and_, delete, insert, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from parserium_collector.adapters.database.tables import (
    hosted_sessions,
    oidc_identities,
    users,
    workspace_invitations,
    workspace_memberships,
    workspaces,
)
from parserium_collector.features.identity.models import (
    HostedIdentityNotFound,
    HostedIdentityRejected,
    HostedLoginResult,
    HostedSessionNotFound,
    HostedSessionRecord,
    InvitationRejected,
    WorkspaceInvitationRecord,
    WorkspaceMembershipNotFound,
    WorkspaceMembershipRecord,
    WorkspaceRole,
)


def _invitation_record(row: RowMapping) -> WorkspaceInvitationRecord:
    return WorkspaceInvitationRecord(
        id=row.id,
        workspace_id=row.workspace_id,
        normalized_email=row.normalized_email,
        token_digest=row.token_digest,
        role=WorkspaceRole(row.role),
        created_at=row.created_at,
        expires_at=row.expires_at,
        accepted_at=row.accepted_at,
        revoked_at=row.revoked_at,
        invited_by_user_id=row.invited_by_user_id,
    )


def _session_record(row: RowMapping) -> HostedSessionRecord:
    return HostedSessionRecord(
        token_digest=row.token_digest,
        user_id=row.user_id,
        workspace_id=row.workspace_id,
        workspace_name=row.workspace_name,
        role=WorkspaceRole(row.role),
        email=row.email,
        display_name=row.display_name,
        created_at=row.created_at,
        last_seen_at=row.last_seen_at,
        revoked_at=row.revoked_at,
        disabled_at=row.disabled_at,
    )


def _login_result(record: HostedSessionRecord) -> HostedLoginResult:
    return HostedLoginResult(
        session_digest=record.token_digest,
        user_id=record.user_id,
        workspace_id=record.workspace_id,
        workspace_name=record.workspace_name,
        role=record.role,
        email=record.email,
        display_name=record.display_name,
        created_at=record.created_at,
        last_seen_at=record.last_seen_at,
    )


class IdentityRepository(Protocol):
    async def create_workspace_invitation(
        self,
        *,
        workspace_name: str | None,
        normalized_email: str,
        token_digest: str,
        role: WorkspaceRole,
        created_at: datetime,
        expires_at: datetime,
        workspace_id: UUID | None,
        invited_by_user_id: UUID | None = None,
    ) -> WorkspaceInvitationRecord: ...

    async def revoke_invitation(self, invitation_id: UUID, now: datetime) -> bool: ...

    async def redeem_invitation_and_create_session(
        self,
        *,
        token_digest: str,
        issuer: str,
        subject: str,
        email: str,
        normalized_email: str,
        display_name: str | None,
        session_digest: str,
        now: datetime,
    ) -> HostedLoginResult: ...

    async def create_session_for_existing_identity(
        self,
        *,
        issuer: str,
        subject: str,
        email: str,
        normalized_email: str,
        display_name: str | None,
        session_digest: str,
        now: datetime,
    ) -> HostedLoginResult: ...

    async def load_hosted_session(self, token_digest: str) -> HostedSessionRecord | None: ...

    async def touch_hosted_session(self, token_digest: str, now: datetime) -> None: ...

    async def revoke_hosted_session(self, token_digest: str, now: datetime) -> None: ...

    async def list_memberships(
        self,
        user_id: UUID,
    ) -> tuple[WorkspaceMembershipRecord, ...]: ...

    async def switch_workspace(
        self,
        *,
        token_digest: str,
        user_id: UUID,
        workspace_id: UUID,
        now: datetime,
    ) -> HostedSessionRecord: ...


class PostgresIdentityRepository:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def create_workspace_invitation(
        self,
        *,
        workspace_name: str | None,
        normalized_email: str,
        token_digest: str,
        role: WorkspaceRole,
        created_at: datetime,
        expires_at: datetime,
        workspace_id: UUID | None,
        invited_by_user_id: UUID | None = None,
    ) -> WorkspaceInvitationRecord:
        if (workspace_name is None) == (workspace_id is None):
            raise ValueError("Exactly one workspace target is required.")
        if workspace_name is not None and role is not WorkspaceRole.OWNER:
            raise ValueError("A new workspace invitation must grant owner role.")
        invitation_id = uuid4()
        async with self._engine.begin() as connection:
            resolved_workspace_id = workspace_id
            if workspace_name is not None:
                resolved_workspace_id = uuid4()
                await connection.execute(
                    insert(workspaces).values(
                        id=resolved_workspace_id,
                        name=workspace_name,
                        is_local=False,
                        created_at=created_at,
                        updated_at=created_at,
                    )
                )
            else:
                existing = (
                    await connection.execute(
                        select(workspaces.c.id).where(workspaces.c.id == workspace_id)
                    )
                ).scalar_one_or_none()
                if existing is None:
                    raise WorkspaceMembershipNotFound
            if resolved_workspace_id is None:
                raise WorkspaceMembershipNotFound
            statement = (
                insert(workspace_invitations)
                .values(
                    id=invitation_id,
                    workspace_id=resolved_workspace_id,
                    normalized_email=normalized_email,
                    token_digest=token_digest,
                    role=role.value,
                    created_at=created_at,
                    expires_at=expires_at,
                    accepted_at=None,
                    revoked_at=None,
                    invited_by_user_id=invited_by_user_id,
                )
                .returning(*workspace_invitations.c)
            )
            row = (await connection.execute(statement)).mappings().one()
        return _invitation_record(row)

    async def revoke_invitation(self, invitation_id: UUID, now: datetime) -> bool:
        statement = (
            update(workspace_invitations)
            .where(
                workspace_invitations.c.id == invitation_id,
                workspace_invitations.c.accepted_at.is_(None),
                workspace_invitations.c.revoked_at.is_(None),
            )
            .values(revoked_at=now)
            .returning(workspace_invitations.c.id)
        )
        async with self._engine.begin() as connection:
            return (await connection.execute(statement)).scalar_one_or_none() is not None

    async def redeem_invitation_and_create_session(
        self,
        *,
        token_digest: str,
        issuer: str,
        subject: str,
        email: str,
        normalized_email: str,
        display_name: str | None,
        session_digest: str,
        now: datetime,
    ) -> HostedLoginResult:
        async with self._engine.begin() as connection:
            invitation = (
                (
                    await connection.execute(
                        select(workspace_invitations)
                        .where(workspace_invitations.c.token_digest == token_digest)
                        .with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if (
                invitation is None
                or invitation.accepted_at is not None
                or invitation.revoked_at is not None
                or invitation.expires_at <= now
                or invitation.normalized_email != normalized_email
            ):
                raise InvitationRejected

            user_id, disabled_at = await self._upsert_identity(
                connection,
                issuer=issuer,
                subject=subject,
                email=email,
                normalized_email=normalized_email,
                display_name=display_name,
                now=now,
            )
            if disabled_at is not None:
                raise HostedIdentityRejected

            membership_role = await self._grant_membership(
                connection,
                workspace_id=invitation.workspace_id,
                user_id=user_id,
                invited_role=WorkspaceRole(invitation.role),
                now=now,
            )
            await connection.execute(
                update(workspace_invitations)
                .where(workspace_invitations.c.id == invitation.id)
                .values(accepted_at=now)
            )
            await connection.execute(
                insert(hosted_sessions).values(
                    token_digest=session_digest,
                    user_id=user_id,
                    workspace_id=invitation.workspace_id,
                    created_at=now,
                    last_seen_at=now,
                    revoked_at=None,
                )
            )
            record = await self._load_hosted_session(connection, session_digest)
            if record is None or record.role is not membership_role:
                raise HostedSessionNotFound
        return _login_result(record)

    async def create_session_for_existing_identity(
        self,
        *,
        issuer: str,
        subject: str,
        email: str,
        normalized_email: str,
        display_name: str | None,
        session_digest: str,
        now: datetime,
    ) -> HostedLoginResult:
        async with self._engine.begin() as connection:
            identity = (
                (
                    await connection.execute(
                        select(users.c.id, users.c.disabled_at)
                        .select_from(
                            oidc_identities.join(users, oidc_identities.c.user_id == users.c.id)
                        )
                        .where(
                            oidc_identities.c.issuer == issuer,
                            oidc_identities.c.subject == subject,
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if identity is None:
                raise HostedIdentityNotFound
            if identity.disabled_at is not None:
                raise HostedIdentityRejected
            await connection.execute(
                update(users)
                .where(users.c.id == identity.id)
                .values(
                    email=email,
                    normalized_email=normalized_email,
                    display_name=display_name,
                    updated_at=now,
                )
            )
            workspace_id = await self._preferred_workspace(connection, identity.id)
            if workspace_id is None:
                raise HostedIdentityRejected
            await connection.execute(
                insert(hosted_sessions).values(
                    token_digest=session_digest,
                    user_id=identity.id,
                    workspace_id=workspace_id,
                    created_at=now,
                    last_seen_at=now,
                    revoked_at=None,
                )
            )
            record = await self._load_hosted_session(connection, session_digest)
            if record is None:
                raise HostedSessionNotFound
        return _login_result(record)

    async def load_hosted_session(self, token_digest: str) -> HostedSessionRecord | None:
        async with self._engine.connect() as connection:
            return await self._load_hosted_session(connection, token_digest)

    async def touch_hosted_session(self, token_digest: str, now: datetime) -> None:
        async with self._engine.begin() as connection:
            await connection.execute(
                update(hosted_sessions)
                .where(
                    hosted_sessions.c.token_digest == token_digest,
                    hosted_sessions.c.revoked_at.is_(None),
                )
                .values(last_seen_at=now)
            )

    async def revoke_hosted_session(self, token_digest: str, now: datetime) -> None:
        async with self._engine.begin() as connection:
            await connection.execute(
                update(hosted_sessions)
                .where(hosted_sessions.c.token_digest == token_digest)
                .values(revoked_at=now)
            )

    async def list_memberships(
        self,
        user_id: UUID,
    ) -> tuple[WorkspaceMembershipRecord, ...]:
        statement = (
            select(
                workspace_memberships.c.workspace_id,
                workspaces.c.name.label("workspace_name"),
                workspace_memberships.c.user_id,
                workspace_memberships.c.role,
                workspace_memberships.c.created_at,
            )
            .select_from(
                workspace_memberships.join(
                    workspaces,
                    workspace_memberships.c.workspace_id == workspaces.c.id,
                )
            )
            .where(workspace_memberships.c.user_id == user_id)
            .order_by(workspace_memberships.c.created_at, workspace_memberships.c.workspace_id)
        )
        async with self._engine.connect() as connection:
            rows = (await connection.execute(statement)).mappings().all()
        return tuple(
            WorkspaceMembershipRecord(
                workspace_id=row.workspace_id,
                workspace_name=row.workspace_name,
                user_id=row.user_id,
                role=WorkspaceRole(row.role),
                created_at=row.created_at,
            )
            for row in rows
        )

    async def switch_workspace(
        self,
        *,
        token_digest: str,
        user_id: UUID,
        workspace_id: UUID,
        now: datetime,
    ) -> HostedSessionRecord:
        async with self._engine.begin() as connection:
            membership = (
                await connection.execute(
                    select(workspace_memberships.c.workspace_id).where(
                        workspace_memberships.c.user_id == user_id,
                        workspace_memberships.c.workspace_id == workspace_id,
                    )
                )
            ).scalar_one_or_none()
            if membership is None:
                raise WorkspaceMembershipNotFound
            changed = (
                await connection.execute(
                    update(hosted_sessions)
                    .where(
                        hosted_sessions.c.token_digest == token_digest,
                        hosted_sessions.c.user_id == user_id,
                        hosted_sessions.c.revoked_at.is_(None),
                    )
                    .values(workspace_id=workspace_id, last_seen_at=now)
                    .returning(hosted_sessions.c.token_digest)
                )
            ).scalar_one_or_none()
            if changed is None:
                raise HostedSessionNotFound
            record = await self._load_hosted_session(connection, token_digest)
            if record is None:
                raise HostedSessionNotFound
            return record

    async def _upsert_identity(
        self,
        connection: AsyncConnection,
        *,
        issuer: str,
        subject: str,
        email: str,
        normalized_email: str,
        display_name: str | None,
        now: datetime,
    ) -> tuple[UUID, datetime | None]:
        identity = (
            (
                await connection.execute(
                    select(users.c.id, users.c.disabled_at)
                    .select_from(
                        oidc_identities.join(users, oidc_identities.c.user_id == users.c.id)
                    )
                    .where(
                        oidc_identities.c.issuer == issuer,
                        oidc_identities.c.subject == subject,
                    )
                    .with_for_update()
                )
            )
            .mappings()
            .one_or_none()
        )
        if identity is None:
            proposed_user_id = uuid4()
            await connection.execute(
                insert(users).values(
                    id=proposed_user_id,
                    email=email,
                    normalized_email=normalized_email,
                    display_name=display_name,
                    created_at=now,
                    updated_at=now,
                    disabled_at=None,
                )
            )
            inserted_user_id = (
                await connection.execute(
                    postgresql_insert(oidc_identities)
                    .values(
                        id=uuid4(),
                        user_id=proposed_user_id,
                        issuer=issuer,
                        subject=subject,
                        created_at=now,
                    )
                    .on_conflict_do_nothing(
                        index_elements=[
                            oidc_identities.c.issuer,
                            oidc_identities.c.subject,
                        ]
                    )
                    .returning(oidc_identities.c.user_id)
                )
            ).scalar_one_or_none()
            if inserted_user_id is not None:
                return inserted_user_id, None
            await connection.execute(delete(users).where(users.c.id == proposed_user_id))
            identity = (
                (
                    await connection.execute(
                        select(users.c.id, users.c.disabled_at)
                        .select_from(
                            oidc_identities.join(users, oidc_identities.c.user_id == users.c.id)
                        )
                        .where(
                            oidc_identities.c.issuer == issuer,
                            oidc_identities.c.subject == subject,
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .one()
            )
        await connection.execute(
            update(users)
            .where(users.c.id == identity.id)
            .values(
                email=email,
                normalized_email=normalized_email,
                display_name=display_name,
                updated_at=now,
            )
        )
        return identity.id, identity.disabled_at

    async def _grant_membership(
        self,
        connection: AsyncConnection,
        *,
        workspace_id: UUID,
        user_id: UUID,
        invited_role: WorkspaceRole,
        now: datetime,
    ) -> WorkspaceRole:
        current = (
            await connection.execute(
                select(workspace_memberships.c.role)
                .where(
                    workspace_memberships.c.workspace_id == workspace_id,
                    workspace_memberships.c.user_id == user_id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if current is None:
            await connection.execute(
                insert(workspace_memberships).values(
                    workspace_id=workspace_id,
                    user_id=user_id,
                    role=invited_role.value,
                    created_at=now,
                )
            )
            return invited_role
        current_role = WorkspaceRole(current)
        if invited_role is WorkspaceRole.OWNER and current_role is WorkspaceRole.MEMBER:
            await connection.execute(
                update(workspace_memberships)
                .where(
                    workspace_memberships.c.workspace_id == workspace_id,
                    workspace_memberships.c.user_id == user_id,
                )
                .values(role=WorkspaceRole.OWNER.value)
            )
            return WorkspaceRole.OWNER
        return current_role

    async def _preferred_workspace(
        self,
        connection: AsyncConnection,
        user_id: UUID,
    ) -> UUID | None:
        statement = (
            select(workspace_memberships.c.workspace_id)
            .select_from(
                workspace_memberships.outerjoin(
                    hosted_sessions,
                    and_(
                        hosted_sessions.c.user_id == workspace_memberships.c.user_id,
                        hosted_sessions.c.workspace_id == workspace_memberships.c.workspace_id,
                    ),
                )
            )
            .where(workspace_memberships.c.user_id == user_id)
            .order_by(
                hosted_sessions.c.last_seen_at.desc().nulls_last(),
                workspace_memberships.c.created_at,
                workspace_memberships.c.workspace_id,
            )
            .limit(1)
        )
        return (await connection.execute(statement)).scalar_one_or_none()

    async def _load_hosted_session(
        self,
        connection: AsyncConnection,
        token_digest: str,
    ) -> HostedSessionRecord | None:
        statement = (
            select(
                hosted_sessions.c.token_digest,
                hosted_sessions.c.user_id,
                hosted_sessions.c.workspace_id,
                workspaces.c.name.label("workspace_name"),
                workspace_memberships.c.role,
                users.c.email,
                users.c.display_name,
                hosted_sessions.c.created_at,
                hosted_sessions.c.last_seen_at,
                hosted_sessions.c.revoked_at,
                users.c.disabled_at,
            )
            .select_from(
                hosted_sessions.join(
                    users,
                    hosted_sessions.c.user_id == users.c.id,
                )
                .join(
                    workspace_memberships,
                    and_(
                        hosted_sessions.c.workspace_id == workspace_memberships.c.workspace_id,
                        hosted_sessions.c.user_id == workspace_memberships.c.user_id,
                    ),
                )
                .join(workspaces, hosted_sessions.c.workspace_id == workspaces.c.id)
            )
            .where(hosted_sessions.c.token_digest == token_digest)
        )
        row = (await connection.execute(statement)).mappings().one_or_none()
        return None if row is None else _session_record(row)
