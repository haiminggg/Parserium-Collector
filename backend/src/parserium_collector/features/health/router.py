from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from parserium_collector.features.health.models import HealthStatus, LiveResponse
from parserium_collector.features.health.service import HealthService
from parserium_collector.settings import Settings

router = APIRouter(prefix="/api/v1/health", tags=["health"])


@router.get("/live", response_model=LiveResponse, operation_id="get_liveness")
async def get_liveness(request: Request) -> LiveResponse:
    settings: Settings = request.app.state.settings
    return LiveResponse(
        status="alive",
        build_id=settings.build_id,
        release_version=settings.release_version,
    )


@router.get("/status", response_model=HealthStatus, operation_id="get_health_status")
async def get_health_status(request: Request) -> HealthStatus:
    service: HealthService = request.app.state.health_service
    return await service.status()


@router.get(
    "/ready",
    response_model=HealthStatus,
    responses={503: {"model": HealthStatus}},
    operation_id="get_readiness",
)
async def get_readiness(request: Request) -> JSONResponse:
    service: HealthService = request.app.state.health_service
    status = await service.status()
    return JSONResponse(
        status_code=200 if status.overall == "ready" else 503,
        content=status.model_dump(mode="json"),
    )
