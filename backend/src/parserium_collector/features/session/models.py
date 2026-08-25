from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


@dataclass(frozen=True)
class SessionRecord:
    token_digest: str
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


@dataclass(frozen=True)
class AuthenticatedSession:
    csrf_token: str
    idle_expires_at: datetime


class PairingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=128)


class PublicSessionStatus(BaseModel):
    status: Literal["pairing_required"]


class SessionResponse(BaseModel):
    status: Literal["authenticated"] = "authenticated"
    csrf_token: str
    idle_expires_at: datetime
