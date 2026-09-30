from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class AuthenticationMode(StrEnum):
    LOCAL = "local"
    OIDC = "oidc"


class WorkspaceRole(StrEnum):
    OWNER = "owner"
    MEMBER = "member"


@dataclass(frozen=True)
class WorkspaceScope:
    workspace_id: UUID
    user_id: UUID | None
    role: WorkspaceRole


@dataclass(frozen=True)
class UserRecord:
    id: UUID
    email: str
    normalized_email: str
    display_name: str | None
    created_at: datetime
    updated_at: datetime
    disabled_at: datetime | None


@dataclass(frozen=True)
class WorkspaceRecord:
    id: UUID
    name: str
    is_local: bool
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class WorkspaceMembershipRecord:
    workspace_id: UUID
    workspace_name: str
    user_id: UUID
    role: WorkspaceRole
    created_at: datetime


@dataclass(frozen=True)
class WorkspaceInvitationRecord:
    id: UUID
    workspace_id: UUID
    normalized_email: str
    token_digest: str
    role: WorkspaceRole
    created_at: datetime
    expires_at: datetime
    accepted_at: datetime | None
    revoked_at: datetime | None
    invited_by_user_id: UUID | None


@dataclass(frozen=True)
class HostedSessionRecord:
    token_digest: str
    user_id: UUID
    workspace_id: UUID
    workspace_name: str
    role: WorkspaceRole
    email: str
    display_name: str | None
    created_at: datetime
    last_seen_at: datetime
    revoked_at: datetime | None
    disabled_at: datetime | None


@dataclass(frozen=True)
class HostedLoginResult:
    session_digest: str
    user_id: UUID
    workspace_id: UUID
    workspace_name: str
    role: WorkspaceRole
    email: str
    display_name: str | None
    created_at: datetime
    last_seen_at: datetime


@dataclass(frozen=True)
class OidcClaims:
    issuer: str
    subject: str
    email: str
    email_verified: bool
    display_name: str | None


@dataclass(frozen=True)
class HostedPrincipal:
    authentication_mode: AuthenticationMode
    token_digest: str
    scope: WorkspaceScope
    workspace_name: str
    email: str
    display_name: str | None
    created_at: datetime
    last_seen_at: datetime
    idle_expires_at: datetime


@dataclass(frozen=True)
class IssuedHostedSession:
    token: str
    authentication_mode: AuthenticationMode
    scope: WorkspaceScope
    workspace_name: str
    email: str
    display_name: str | None
    created_at: datetime
    last_seen_at: datetime
    idle_expires_at: datetime


class OidcAuthenticationFailed(RuntimeError):
    def __init__(self) -> None:
        super().__init__("OpenID Connect authentication failed.")


class HostedIdentityNotFound(RuntimeError):
    def __init__(self) -> None:
        super().__init__("The hosted identity is unavailable.")


class InvitationRejected(RuntimeError):
    def __init__(self) -> None:
        super().__init__("The invitation is invalid or unavailable.")


class InvitationRequired(RuntimeError):
    def __init__(self) -> None:
        super().__init__("An invitation is required.")


class HostedIdentityRejected(RuntimeError):
    def __init__(self) -> None:
        super().__init__("The hosted identity cannot sign in.")


class HostedSessionNotFound(RuntimeError):
    def __init__(self) -> None:
        super().__init__("The hosted session is unavailable.")


class WorkspaceMembershipNotFound(RuntimeError):
    def __init__(self) -> None:
        super().__init__("The workspace membership is unavailable.")
