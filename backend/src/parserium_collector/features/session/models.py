from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from parserium_collector.features.identity.models import (
    AuthenticationMode,
    WorkspaceRole,
    WorkspaceScope,
)


class WorkspaceSummary(BaseModel):
    id: UUID
    name: str
    role: WorkspaceRole


class UserSummary(BaseModel):
    id: UUID
    email: str
    display_name: str | None


@dataclass(frozen=True)
class SessionRecord:
    token_digest: str
    workspace_id: UUID
    workspace_name: str
    created_at: datetime
    last_seen_at: datetime
    revoked_at: datetime | None


@dataclass(frozen=True)
class IssuedPairingCode:
    code: str
    expires_at: datetime


@dataclass(frozen=True)
class IssuedSession:
    token: str
    csrf_token: str
    idle_expires_at: datetime
    authentication_mode: AuthenticationMode
    scope: WorkspaceScope
    workspace_name: str
    email: str | None
    display_name: str | None
    workspaces: tuple[WorkspaceSummary, ...]


@dataclass(frozen=True)
class AuthenticatedSession:
    token_digest: str
    csrf_token: str
    idle_expires_at: datetime
    authentication_mode: AuthenticationMode
    scope: WorkspaceScope
    workspace_name: str
    email: str | None
    display_name: str | None
    workspaces: tuple[WorkspaceSummary, ...]


class PairingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=128)


class PublicSessionStatus(BaseModel):
    status: Literal["pairing_required"]


class LoginRequiredSession(BaseModel):
    status: Literal["login_required"] = "login_required"
    login_url: str = "/api/v1/auth/login"
    provider_label: str


class SessionResponse(BaseModel):
    status: Literal["authenticated"] = "authenticated"
    csrf_token: str
    idle_expires_at: datetime
    authentication_mode: AuthenticationMode
    user: UserSummary | None
    workspace: WorkspaceSummary
    workspaces: tuple[WorkspaceSummary, ...]


class WorkspaceSwitchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: UUID
