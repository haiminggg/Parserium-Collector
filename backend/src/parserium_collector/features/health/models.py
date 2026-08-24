from enum import StrEnum
from typing import Literal

from pydantic import BaseModel


class LiveResponse(BaseModel):
    status: Literal["alive"]
    build_id: str
    release_version: str


class ComponentStatus(StrEnum):
    AVAILABLE = "available"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    UNSUPPORTED = "unsupported"
    NOT_CONFIGURED = "not_configured"


class ComponentHealth(BaseModel):
    name: str
    status: ComponentStatus
    detail: str | None = None
    required: bool = True


class HealthStatus(BaseModel):
    overall: Literal["ready", "degraded"]
    build_id: str
    components: list[ComponentHealth]
