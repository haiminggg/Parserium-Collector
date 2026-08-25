from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from parserium_collector.features.session.errors import (
    InvalidCsrfToken,
    InvalidPairingCode,
    InvalidSession,
)
from parserium_collector.features.session.models import SessionRecord
from parserium_collector.features.session.service import SessionService


class InMemorySessionRepository:
    def __init__(self) -> None:
        self.pairings: dict[str, tuple[datetime, datetime | None]] = {}
        self.sessions: dict[str, SessionRecord] = {}

    async def replace_pairing_code(
        self,
        code_digest: str,
        created_at: datetime,
        expires_at: datetime,
    ) -> None:
        self.pairings = {code_digest: (expires_at, None)}

    async def exchange_pairing_code(
        self,
        code_digest: str,
        token_digest: str,
        now: datetime,
    ) -> bool:
        pairing = self.pairings.get(code_digest)
        if pairing is None or pairing[0] <= now or pairing[1] is not None:
            return False
        self.pairings[code_digest] = (pairing[0], now)
        self.sessions[token_digest] = SessionRecord(
            token_digest=token_digest,
            created_at=now,
            last_seen_at=now,
            revoked_at=None,
        )
        return True

    async def load_session(self, token_digest: str) -> SessionRecord | None:
        return self.sessions.get(token_digest)

    async def touch_session(self, token_digest: str, now: datetime) -> None:
        self.sessions[token_digest] = replace(self.sessions[token_digest], last_seen_at=now)

    async def revoke_session(self, token_digest: str, now: datetime) -> None:
        self.sessions[token_digest] = replace(self.sessions[token_digest], revoked_at=now)

    async def has_active_session(self, active_after: datetime) -> bool:
        return any(
            session.revoked_at is None and session.last_seen_at > active_after
            for session in self.sessions.values()
        )

    async def has_active_pairing(self, now: datetime) -> bool:
        return any(
            expires_at > now and used_at is None
            for expires_at, used_at in self.pairings.values()
        )

    async def revoke_all_and_replace_pairing(
        self,
        code_digest: str,
        created_at: datetime,
        expires_at: datetime,
    ) -> None:
        self.sessions = {
            digest: replace(session, revoked_at=created_at)
            for digest, session in self.sessions.items()
        }
        self.pairings = {code_digest: (expires_at, None)}


def service(repository: InMemorySessionRepository) -> SessionService:
    return SessionService(
        repository=repository,
        signing_secret=b"k" * 32,
        pairing_ttl=timedelta(minutes=10),
        session_idle_ttl=timedelta(hours=24),
    )


async def test_pairing_code_is_one_time_and_raw_credentials_are_not_stored() -> None:
    repository = InMemorySessionRepository()
    sessions = service(repository)
    now = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)

    pairing = await sessions.issue_pairing_code(now)
    issued = await sessions.pair(pairing.code, now + timedelta(minutes=1))

    assert pairing.code not in repository.pairings
    assert issued.token not in repository.sessions
    assert issued.csrf_token != issued.token
    with pytest.raises(InvalidPairingCode):
        await sessions.pair(pairing.code, now + timedelta(minutes=2))


async def test_startup_preserves_unused_pairing_and_stops_after_authentication() -> None:
    repository = InMemorySessionRepository()
    sessions = service(repository)
    now = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)

    pairing = await sessions.ensure_pairing_code(now)
    assert pairing is not None

    assert await sessions.ensure_pairing_code(now + timedelta(minutes=1)) is None
    await sessions.pair(pairing.code, now)

    assert await sessions.ensure_pairing_code(now + timedelta(minutes=1)) is None


async def test_expired_pairing_code_does_not_create_session() -> None:
    repository = InMemorySessionRepository()
    sessions = service(repository)
    now = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)
    pairing = await sessions.issue_pairing_code(now)

    with pytest.raises(InvalidPairingCode):
        await sessions.pair(pairing.code, now + timedelta(minutes=10))

    assert repository.sessions == {}


async def test_authentication_refreshes_idle_time_and_returns_csrf_token() -> None:
    repository = InMemorySessionRepository()
    sessions = service(repository)
    now = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)
    pairing = await sessions.issue_pairing_code(now)
    issued = await sessions.pair(pairing.code, now)

    authenticated = await sessions.authenticate(issued.token, now + timedelta(hours=1))

    assert authenticated.csrf_token == issued.csrf_token
    assert authenticated.idle_expires_at == now + timedelta(hours=25)


async def test_idle_expired_or_revoked_session_is_rejected() -> None:
    repository = InMemorySessionRepository()
    sessions = service(repository)
    now = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)
    pairing = await sessions.issue_pairing_code(now)
    issued = await sessions.pair(pairing.code, now)

    with pytest.raises(InvalidSession):
        await sessions.authenticate(issued.token, now + timedelta(hours=24))


async def test_logout_requires_csrf_and_revokes_session() -> None:
    repository = InMemorySessionRepository()
    sessions = service(repository)
    now = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)
    pairing = await sessions.issue_pairing_code(now)
    issued = await sessions.pair(pairing.code, now)

    with pytest.raises(InvalidCsrfToken):
        await sessions.logout(issued.token, "wrong", now + timedelta(minutes=1))

    await sessions.logout(issued.token, issued.csrf_token, now + timedelta(minutes=1))
    with pytest.raises(InvalidSession):
        await sessions.authenticate(issued.token, now + timedelta(minutes=2))


async def test_recovery_revokes_sessions_and_replaces_pairing_code() -> None:
    repository = InMemorySessionRepository()
    sessions = service(repository)
    now = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)
    original_pairing = await sessions.issue_pairing_code(now)
    issued = await sessions.pair(original_pairing.code, now)

    replacement = await sessions.recover(now + timedelta(minutes=1))

    with pytest.raises(InvalidSession):
        await sessions.authenticate(issued.token, now + timedelta(minutes=2))
    with pytest.raises(InvalidPairingCode):
        await sessions.pair(original_pairing.code, now + timedelta(minutes=2))
    assert await sessions.pair(replacement.code, now + timedelta(minutes=2))
