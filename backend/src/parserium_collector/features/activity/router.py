from datetime import UTC, datetime
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status

from parserium_collector.features.activity.errors import (
    ActivityConflictError,
    ActivityNotFoundError,
)
from parserium_collector.features.activity.models import (
    ActivityJobState,
    ActivityJobType,
    ActivityPageResponse,
)
from parserium_collector.features.activity.service import ActivityService
from parserium_collector.features.session.dependencies import (
    require_authenticated_session,
    require_csrf_session,
)
from parserium_collector.features.session.models import AuthenticatedSession

router = APIRouter(prefix="/api/v1/activity", tags=["activity"])


def _service(request: Request) -> ActivityService:
    service = cast(
        ActivityService | None,
        getattr(request.app.state, "activity_service", None),
    )
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Activity history is not configured.",
        )
    return service


@router.get("", response_model=ActivityPageResponse, operation_id="list_activity")
async def list_activity(
    request: Request,
    authenticated: Annotated[AuthenticatedSession, Depends(require_authenticated_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    job_type: ActivityJobType | None = None,
    state: ActivityJobState | None = None,
    creator_id: UUID | None = None,
    created_after: datetime | None = None,
) -> ActivityPageResponse:
    try:
        return await _service(request).list_activity(
            authenticated.scope,
            limit=limit,
            cursor=cursor,
            job_type=job_type,
            state=state,
            creator_id=creator_id,
            created_after=created_after,
        )
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="The activity pagination cursor is invalid.",
        ) from error


@router.delete(
    "/{job_type}/{job_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="delete_activity_history",
)
async def delete_activity_history(
    job_type: ActivityJobType,
    job_id: UUID,
    request: Request,
    authenticated: Annotated[AuthenticatedSession, Depends(require_csrf_session)],
) -> Response:
    try:
        await _service(request).delete_history(
            authenticated.scope,
            job_type,
            job_id,
            datetime.now(UTC),
        )
    except ActivityNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Activity item not found.",
        ) from error
    except ActivityConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Active activity history cannot be deleted.",
        ) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)
