import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import URL, update
from sqlalchemy.ext.asyncio import create_async_engine

from parserium_collector.adapters.database.tables import metadata, users
from parserium_collector.features.identity.models import (
    HostedIdentityRejected,
    InvitationRejected,
    WorkspaceInvitationRecord,
    WorkspaceMembershipNotFound,
    WorkspaceRole,
)
from parserium_collector.features.identity.repository import PostgresIdentityRepository

NOW = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)


def _database_url() -> str:
    password_file = Path(os.environ["TEST_DATABASE_PASSWORD_FILE"])
    return URL.create(
        "postgresql+psycopg",
        username="parserium_collector",
        password=password_file.read_text(encoding="utf-8").strip(),
        host=os.environ["TEST_DATABASE_HOST"],
        port=5432,
        database="parserium_collector",
    ).render_as_string(hide_password=False)


@pytest.fixture
async def identity_repository() -> AsyncIterator[PostgresIdentityRepository]:
    engine = create_async_engine(_database_url())
    async with engine.begin() as connection:
        await connection.run_sync(metadata.drop_all)
        await connection.run_sync(metadata.create_all)
    repository = PostgresIdentityRepository(engine)
    try:
        yield repository
    finally:
        async with engine.begin() as connection:
            await connection.run_sync(metadata.drop_all)
        await engine.dispose()


async def _invite(
    repository: PostgresIdentityRepository,
    *,
    email: str,
    digest: str,
    workspace_name: str,
) -> WorkspaceInvitationRecord:
    return await repository.create_workspace_invitation(
        workspace_name=workspace_name,
        workspace_id=None,
        normalized_email=email,
        token_digest=digest,
        role=WorkspaceRole.OWNER,
        created_at=NOW,
        expires_at=NOW + timedelta(hours=24),
    )


@pytest.mark.asyncio
async def test_invitation_redemption_creates_identity_membership_and_session(
    identity_repository: PostgresIdentityRepository,
) -> None:
    invitation = await _invite(
        identity_repository,
        email="owner@northbridge.example",
        digest="a" * 64,
        workspace_name="Northbridge",
    )

    issued = await identity_repository.redeem_invitation_and_create_session(
        token_digest="a" * 64,
        issuer="https://identity.parserium.test",
        subject="oidc-user-1",
        email="owner@northbridge.example",
        normalized_email="owner@northbridge.example",
        display_name="Northbridge Owner",
        session_digest="b" * 64,
        now=NOW + timedelta(minutes=1),
    )

    assert issued.workspace_id == invitation.workspace_id
    assert issued.role is WorkspaceRole.OWNER
    assert issued.email == "owner@northbridge.example"
    assert (await identity_repository.load_hosted_session("b" * 64)) is not None


@pytest.mark.asyncio
async def test_invitation_rejects_wrong_email_expiry_replay_and_revocation(
    identity_repository: PostgresIdentityRepository,
) -> None:
    await _invite(
        identity_repository,
        email="owner@example.test",
        digest="c" * 64,
        workspace_name="Wrong email",
    )
    with pytest.raises(InvitationRejected):
        await identity_repository.redeem_invitation_and_create_session(
            token_digest="c" * 64,
            issuer="https://identity.parserium.test",
            subject="wrong-email",
            email="other@example.test",
            normalized_email="other@example.test",
            display_name=None,
            session_digest="d" * 64,
            now=NOW,
        )

    expired = await identity_repository.create_workspace_invitation(
        workspace_name="Expired",
        workspace_id=None,
        normalized_email="expired@example.test",
        token_digest="e" * 64,
        role=WorkspaceRole.OWNER,
        created_at=NOW - timedelta(days=2),
        expires_at=NOW - timedelta(days=1),
    )
    with pytest.raises(InvitationRejected):
        await identity_repository.redeem_invitation_and_create_session(
            token_digest="e" * 64,
            issuer="https://identity.parserium.test",
            subject="expired",
            email="expired@example.test",
            normalized_email="expired@example.test",
            display_name=None,
            session_digest="f" * 64,
            now=NOW,
        )

    revoked = await _invite(
        identity_repository,
        email="revoked@example.test",
        digest="1" * 64,
        workspace_name="Revoked",
    )
    assert await identity_repository.revoke_invitation(revoked.id, NOW)
    with pytest.raises(InvitationRejected):
        await identity_repository.redeem_invitation_and_create_session(
            token_digest="1" * 64,
            issuer="https://identity.parserium.test",
            subject="revoked",
            email="revoked@example.test",
            normalized_email="revoked@example.test",
            display_name=None,
            session_digest="2" * 64,
            now=NOW,
        )

    await identity_repository.redeem_invitation_and_create_session(
        token_digest="c" * 64,
        issuer="https://identity.parserium.test",
        subject="valid",
        email="owner@example.test",
        normalized_email="owner@example.test",
        display_name=None,
        session_digest="3" * 64,
        now=NOW,
    )
    with pytest.raises(InvitationRejected):
        await identity_repository.redeem_invitation_and_create_session(
            token_digest="c" * 64,
            issuer="https://identity.parserium.test",
            subject="valid",
            email="owner@example.test",
            normalized_email="owner@example.test",
            display_name=None,
            session_digest="4" * 64,
            now=NOW,
        )
    assert expired.id != revoked.id


@pytest.mark.asyncio
async def test_existing_identity_refreshes_claims_and_uses_most_recent_workspace(
    identity_repository: PostgresIdentityRepository,
) -> None:
    first = await _invite(
        identity_repository,
        email="owner@example.test",
        digest="5" * 64,
        workspace_name="First",
    )
    first_login = await identity_repository.redeem_invitation_and_create_session(
        token_digest="5" * 64,
        issuer="https://identity.parserium.test",
        subject="stable-subject",
        email="owner@example.test",
        normalized_email="owner@example.test",
        display_name="Original",
        session_digest="6" * 64,
        now=NOW,
    )
    second = await _invite(
        identity_repository,
        email="owner@example.test",
        digest="7" * 64,
        workspace_name="Second",
    )
    await identity_repository.redeem_invitation_and_create_session(
        token_digest="7" * 64,
        issuer="https://identity.parserium.test",
        subject="stable-subject",
        email="owner@example.test",
        normalized_email="owner@example.test",
        display_name="Original",
        session_digest="8" * 64,
        now=NOW + timedelta(minutes=5),
    )

    issued = await identity_repository.create_session_for_existing_identity(
        issuer="https://identity.parserium.test",
        subject="stable-subject",
        email="renamed@example.test",
        normalized_email="renamed@example.test",
        display_name="Renamed",
        session_digest="9" * 64,
        now=NOW + timedelta(minutes=10),
    )

    assert issued.user_id == first_login.user_id
    assert issued.workspace_id == second.workspace_id
    assert issued.workspace_id != first.workspace_id
    assert issued.email == "renamed@example.test"
    assert issued.display_name == "Renamed"


@pytest.mark.asyncio
async def test_disabled_identity_and_nonmember_workspace_are_rejected(
    identity_repository: PostgresIdentityRepository,
) -> None:
    invitation = await _invite(
        identity_repository,
        email="owner@example.test",
        digest="a1" * 32,
        workspace_name="Owner",
    )
    login = await identity_repository.redeem_invitation_and_create_session(
        token_digest="a1" * 32,
        issuer="https://identity.parserium.test",
        subject="owner",
        email="owner@example.test",
        normalized_email="owner@example.test",
        display_name=None,
        session_digest="b1" * 32,
        now=NOW,
    )
    other = await _invite(
        identity_repository,
        email="other@example.test",
        digest="c1" * 32,
        workspace_name="Other",
    )

    with pytest.raises(WorkspaceMembershipNotFound):
        await identity_repository.switch_workspace(
            token_digest="b1" * 32,
            user_id=login.user_id,
            workspace_id=other.workspace_id,
            now=NOW,
        )

    engine = create_async_engine(_database_url())
    async with engine.begin() as connection:
        await connection.execute(
            update(users).where(users.c.id == login.user_id).values(disabled_at=NOW)
        )
    await engine.dispose()
    with pytest.raises(HostedIdentityRejected):
        await identity_repository.create_session_for_existing_identity(
            issuer="https://identity.parserium.test",
            subject="owner",
            email="owner@example.test",
            normalized_email="owner@example.test",
            display_name=None,
            session_digest="d1" * 32,
            now=NOW,
        )
    assert invitation.workspace_id == login.workspace_id
