import hmac
from datetime import datetime, timedelta
from uuid import UUID

from parserium_collector.features.identity.models import (
    AuthenticationMode,
    HostedIdentityNotFound,
    HostedIdentityRejected,
    HostedLoginResult,
    HostedSessionNotFound,
    HostedSessionRecord,
    InvitationRequired,
    OidcClaims,
    WorkspaceScope,
)
from parserium_collector.features.identity.repository import IdentityRepository
from parserium_collector.features.session.crypto import (
    csrf_token,
    generate_session_token,
    keyed_digest,
)
from parserium_collector.features.session.errors import InvalidCsrfToken
from parserium_collector.features.session.models import (
    AuthenticatedSession,
    IssuedSession,
    WorkspaceSummary,
)


class IdentityService:
    def __init__(
        self,
        *,
        repository: IdentityRepository,
        signing_secret: bytes,
        session_idle_ttl: timedelta = timedelta(days=1),
    ) -> None:
        self._repository = repository
        self._signing_secret = signing_secret
        self._session_idle_ttl = session_idle_ttl

    async def complete_login(
        self,
        claims: OidcClaims,
        *,
        invitation_digest: str | None,
        now: datetime,
    ) -> IssuedSession:
        if claims.email_verified is not True:
            raise HostedIdentityRejected
        raw_token = generate_session_token()
        session_digest = self._session_digest(raw_token)
        if invitation_digest is None:
            try:
                result = await self._repository.create_session_for_existing_identity(
                    issuer=claims.issuer,
                    subject=claims.subject,
                    email=claims.email,
                    normalized_email=claims.email.casefold(),
                    display_name=claims.display_name,
                    session_digest=session_digest,
                    now=now,
                )
            except HostedIdentityNotFound:
                raise InvitationRequired from None
        else:
            result = await self._repository.redeem_invitation_and_create_session(
                token_digest=invitation_digest,
                issuer=claims.issuer,
                subject=claims.subject,
                email=claims.email,
                normalized_email=claims.email.casefold(),
                display_name=claims.display_name,
                session_digest=session_digest,
                now=now,
            )
        return self._issued(raw_token, result)

    async def authenticate(self, raw_token: str, now: datetime) -> AuthenticatedSession:
        token_digest = self._session_digest(raw_token)
        record = await self._repository.load_hosted_session(token_digest)
        self._require_active(record, now)
        await self._repository.touch_hosted_session(token_digest, now)
        if record is None:
            raise HostedSessionNotFound
        memberships = await self._repository.list_memberships(record.user_id)
        return self._authenticated(
            raw_token,
            record,
            last_seen_at=now,
            workspaces=tuple(
                WorkspaceSummary(
                    id=membership.workspace_id,
                    name=membership.workspace_name,
                    role=membership.role,
                )
                for membership in memberships
            ),
        )

    async def switch_workspace(
        self,
        raw_token: str,
        workspace_id: UUID,
        now: datetime,
    ) -> AuthenticatedSession:
        token_digest = self._session_digest(raw_token)
        current = await self._repository.load_hosted_session(token_digest)
        self._require_active(current, now)
        if current is None:
            raise HostedSessionNotFound
        changed = await self._repository.switch_workspace(
            token_digest=token_digest,
            user_id=current.user_id,
            workspace_id=workspace_id,
            now=now,
        )
        memberships = await self._repository.list_memberships(current.user_id)
        return self._authenticated(
            raw_token,
            changed,
            last_seen_at=now,
            workspaces=tuple(
                WorkspaceSummary(
                    id=membership.workspace_id,
                    name=membership.workspace_name,
                    role=membership.role,
                )
                for membership in memberships
            ),
        )

    async def logout(
        self,
        raw_token: str,
        supplied_csrf_token: str,
        now: datetime,
    ) -> None:
        authenticated = await self.authenticate(raw_token, now)
        if not hmac.compare_digest(authenticated.csrf_token, supplied_csrf_token):
            raise InvalidCsrfToken
        await self._repository.revoke_hosted_session(
            self._session_digest(raw_token),
            now,
        )

    def _session_digest(self, raw_token: str) -> str:
        return keyed_digest(self._signing_secret, "session", raw_token)

    def _require_active(
        self,
        record: HostedSessionRecord | None,
        now: datetime,
    ) -> None:
        if (
            record is None
            or record.revoked_at is not None
            or record.disabled_at is not None
            or record.last_seen_at + self._session_idle_ttl <= now
        ):
            raise HostedSessionNotFound

    def _issued(
        self,
        raw_token: str,
        result: HostedLoginResult,
    ) -> IssuedSession:
        current_workspace = WorkspaceSummary(
            id=result.workspace_id,
            name=result.workspace_name,
            role=result.role,
        )
        return IssuedSession(
            token=raw_token,
            csrf_token=csrf_token(self._signing_secret, raw_token),
            idle_expires_at=result.last_seen_at + self._session_idle_ttl,
            authentication_mode=AuthenticationMode.OIDC,
            scope=WorkspaceScope(
                workspace_id=result.workspace_id,
                user_id=result.user_id,
                role=result.role,
            ),
            workspace_name=result.workspace_name,
            email=result.email,
            display_name=result.display_name,
            workspaces=(current_workspace,),
        )

    def _authenticated(
        self,
        raw_token: str,
        record: HostedSessionRecord,
        *,
        last_seen_at: datetime,
        workspaces: tuple[WorkspaceSummary, ...],
    ) -> AuthenticatedSession:
        return AuthenticatedSession(
            authentication_mode=AuthenticationMode.OIDC,
            token_digest=record.token_digest,
            csrf_token=csrf_token(self._signing_secret, raw_token),
            idle_expires_at=last_seen_at + self._session_idle_ttl,
            scope=WorkspaceScope(
                workspace_id=record.workspace_id,
                user_id=record.user_id,
                role=record.role,
            ),
            workspace_name=record.workspace_name,
            email=record.email,
            display_name=record.display_name,
            workspaces=workspaces,
        )
