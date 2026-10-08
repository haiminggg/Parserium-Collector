from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest

from parserium_collector.features.identity import service as service_module
from parserium_collector.features.identity.models import (
    AuthenticationMode,
    HostedIdentityNotFound,
    HostedIdentityRejected,
    HostedLoginResult,
    InvitationRequired,
    OidcClaims,
    WorkspaceRole,
)
from parserium_collector.features.identity.service import IdentityService
from parserium_collector.features.session.crypto import keyed_digest

NOW = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
WORKSPACE_ID = UUID("10000000-0000-4000-8000-000000000001")
USER_ID = UUID("20000000-0000-4000-8000-000000000001")
ISSUED_VALUE = "opaque-parserium-session"
CLAIMS = OidcClaims(
    issuer="https://identity.parserium.test",
    subject="subject-123",
    email="Owner@Parserium.Test",
    email_verified=True,
    display_name="Parserium Owner",
)


def _result(session_digest: str) -> HostedLoginResult:
    return HostedLoginResult(
        session_digest=session_digest,
        user_id=USER_ID,
        workspace_id=WORKSPACE_ID,
        workspace_name="Northbridge",
        role=WorkspaceRole.OWNER,
        email=CLAIMS.email,
        display_name=CLAIMS.display_name,
        created_at=NOW,
        last_seen_at=NOW,
    )


class RecordingRepository:
    def __init__(self, *, identity_exists: bool = True) -> None:
        self.identity_exists = identity_exists
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.persisted_session_digests: list[str] = []

    async def create_session_for_existing_identity(self, **values: Any) -> HostedLoginResult:
        self.calls.append(("existing", values))
        if not self.identity_exists:
            raise HostedIdentityNotFound
        self.persisted_session_digests.append(values["session_digest"])
        return _result(values["session_digest"])

    async def redeem_invitation_and_create_session(self, **values: Any) -> HostedLoginResult:
        self.calls.append(("redeem", values))
        self.persisted_session_digests.append(values["session_digest"])
        return _result(values["session_digest"])


@pytest.fixture
def repository() -> RecordingRepository:
    return RecordingRepository()


@pytest.fixture
def service(
    repository: RecordingRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> IdentityService:
    monkeypatch.setattr(
        service_module,
        "generate_session_token",
        lambda: ISSUED_VALUE,
    )
    return IdentityService(repository=repository, signing_secret=b"s" * 32)


@pytest.mark.asyncio
async def test_new_identity_requires_matching_invitation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = RecordingRepository(identity_exists=False)
    monkeypatch.setattr(service_module, "generate_session_token", lambda: ISSUED_VALUE)
    service = IdentityService(repository=repository, signing_secret=b"s" * 32)

    with pytest.raises(InvitationRequired):
        await service.complete_login(CLAIMS, invitation_digest=None, now=NOW)


@pytest.mark.asyncio
async def test_verified_invited_identity_receives_opaque_parserium_session(
    service: IdentityService,
    repository: RecordingRepository,
) -> None:
    issued = await service.complete_login(
        CLAIMS,
        invitation_digest="a" * 64,
        now=NOW,
    )

    assert issued.token not in repository.persisted_session_digests
    assert issued.authentication_mode is AuthenticationMode.OIDC
    assert issued.scope.workspace_id == WORKSPACE_ID
    assert repository.persisted_session_digests == [
        keyed_digest(b"s" * 32, "session", ISSUED_VALUE)
    ]
    assert repository.calls[0][0] == "redeem"
    assert repository.calls[0][1]["token_digest"] == "a" * 64
    assert repository.calls[0][1]["normalized_email"] == "owner@parserium.test"


@pytest.mark.asyncio
async def test_unverified_email_is_rejected_before_repository_write(
    service: IdentityService,
    repository: RecordingRepository,
) -> None:
    with pytest.raises(HostedIdentityRejected):
        await service.complete_login(
            replace(CLAIMS, email_verified=False),
            invitation_digest="a" * 64,
            now=NOW,
        )
    assert repository.calls == []
