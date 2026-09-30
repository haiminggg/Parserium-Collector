import hmac
from datetime import datetime, timedelta

from parserium_collector.features.identity.models import (
    AuthenticationMode,
    WorkspaceRole,
    WorkspaceScope,
)
from parserium_collector.features.session.crypto import (
    csrf_token,
    generate_pairing_code,
    generate_session_token,
    keyed_digest,
)
from parserium_collector.features.session.errors import (
    InvalidCsrfToken,
    InvalidPairingCode,
    InvalidSession,
)
from parserium_collector.features.session.models import (
    AuthenticatedSession,
    IssuedPairingCode,
    IssuedSession,
    WorkspaceSummary,
)
from parserium_collector.features.session.repository import SessionRepository


class SessionService:
    def __init__(
        self,
        repository: SessionRepository,
        signing_secret: bytes,
        pairing_ttl: timedelta,
        session_idle_ttl: timedelta,
    ) -> None:
        self._repository = repository
        self._signing_secret = signing_secret
        self._pairing_ttl = pairing_ttl
        self._session_idle_ttl = session_idle_ttl

    async def ensure_pairing_code(self, now: datetime) -> IssuedPairingCode | None:
        if await self._repository.has_active_session(now - self._session_idle_ttl):
            return None
        if await self._repository.has_active_pairing(now):
            return None
        return await self.issue_pairing_code(now)

    async def issue_pairing_code(self, now: datetime) -> IssuedPairingCode:
        code = generate_pairing_code()
        expires_at = now + self._pairing_ttl
        await self._repository.replace_pairing_code(
            keyed_digest(self._signing_secret, "pairing", code),
            now,
            expires_at,
        )
        return IssuedPairingCode(code=code, expires_at=expires_at)

    async def recover(self, now: datetime) -> IssuedPairingCode:
        code = generate_pairing_code()
        expires_at = now + self._pairing_ttl
        await self._repository.revoke_all_and_replace_pairing(
            keyed_digest(self._signing_secret, "pairing", code),
            now,
            expires_at,
        )
        return IssuedPairingCode(code=code, expires_at=expires_at)

    async def pair(self, code: str, now: datetime) -> IssuedSession:
        token = generate_session_token()
        record = await self._repository.exchange_pairing_code(
            keyed_digest(self._signing_secret, "pairing", code),
            keyed_digest(self._signing_secret, "session", token),
            now,
        )
        if record is None:
            raise InvalidPairingCode
        scope = WorkspaceScope(
            workspace_id=record.workspace_id,
            user_id=None,
            role=WorkspaceRole.OWNER,
        )
        workspace = WorkspaceSummary(
            id=record.workspace_id,
            name=record.workspace_name,
            role=WorkspaceRole.OWNER,
        )
        return IssuedSession(
            token=token,
            csrf_token=csrf_token(self._signing_secret, token),
            idle_expires_at=now + self._session_idle_ttl,
            authentication_mode=AuthenticationMode.LOCAL,
            scope=scope,
            workspace_name=record.workspace_name,
            email=None,
            display_name=None,
            workspaces=(workspace,),
        )

    async def authenticate(self, token: str, now: datetime) -> AuthenticatedSession:
        token_digest = keyed_digest(self._signing_secret, "session", token)
        record = await self._repository.load_session(token_digest)
        if (
            record is None
            or record.revoked_at is not None
            or record.last_seen_at + self._session_idle_ttl <= now
        ):
            raise InvalidSession
        await self._repository.touch_session(token_digest, now)
        return AuthenticatedSession(
            token_digest=token_digest,
            csrf_token=csrf_token(self._signing_secret, token),
            idle_expires_at=now + self._session_idle_ttl,
            authentication_mode=AuthenticationMode.LOCAL,
            scope=WorkspaceScope(
                workspace_id=record.workspace_id,
                user_id=None,
                role=WorkspaceRole.OWNER,
            ),
            workspace_name=record.workspace_name,
            email=None,
            display_name=None,
            workspaces=(
                WorkspaceSummary(
                    id=record.workspace_id,
                    name=record.workspace_name,
                    role=WorkspaceRole.OWNER,
                ),
            ),
        )

    async def logout(self, token: str, supplied_csrf_token: str, now: datetime) -> None:
        authenticated = await self.authenticate(token, now)
        if not hmac.compare_digest(authenticated.csrf_token, supplied_csrf_token):
            raise InvalidCsrfToken
        await self._repository.revoke_session(
            keyed_digest(self._signing_secret, "session", token),
            now,
        )
