from datetime import UTC, datetime
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse

from parserium_collector.adapters.firecrawl.errors import FirecrawlAdapterError
from parserium_collector.features.analysis.errors import (
    AnalysisExpiredError,
    AnalysisNotFoundError,
    AnalysisPreviewUnavailableError,
    AnalysisRetryConflictError,
)
from parserium_collector.features.analysis.models import (
    AnalysisSessionResponse,
    DurableAnalysisSearchRequest,
)
from parserium_collector.features.analysis.service import AnalysisService
from parserium_collector.features.session.dependencies import (
    require_authenticated_session,
    require_csrf_session,
)
from parserium_collector.features.session.models import AuthenticatedSession
from parserium_collector.features.storage.errors import ArtifactStorageError

router = APIRouter(prefix="/api/v1/discovery", tags=["analysis"])


def analysis_service(request: Request) -> AnalysisService:
    service = cast(
        AnalysisService | None,
        getattr(request.app.state, "analysis_service", None),
    )
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Document analysis is not configured.",
        )
    return service


@router.post(
    "/searches",
    response_model=AnalysisSessionResponse,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="create_analysis_search",
)
async def create_analysis_search(
    payload: DurableAnalysisSearchRequest,
    authenticated: Annotated[AuthenticatedSession, Depends(require_csrf_session)],
    service: Annotated[AnalysisService, Depends(analysis_service)],
) -> AnalysisSessionResponse:
    try:
        return await service.start_search(
            authenticated.scope,
            payload,
            datetime.now(UTC),
        )
    except FirecrawlAdapterError as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "firecrawl_discovery_unavailable",
                "message": (
                    "Firecrawl document discovery is unavailable. "
                    "Test the selected connection and try again."
                ),
            },
        ) from error
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Document analysis could not be started.",
        ) from error


@router.get(
    "/searches/{session_id}",
    response_model=AnalysisSessionResponse,
    operation_id="get_analysis_search",
)
async def get_analysis_search(
    session_id: UUID,
    authenticated: Annotated[
        AuthenticatedSession,
        Depends(require_authenticated_session),
    ],
    service: Annotated[AnalysisService, Depends(analysis_service)],
) -> AnalysisSessionResponse:
    try:
        return await service.get_search(
            authenticated.scope,
            session_id,
            datetime.now(UTC),
        )
    except AnalysisNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Analysis search not found.",
        ) from error
    except AnalysisExpiredError as error:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Analysis search expired.",
        ) from error
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Document analysis status is unavailable.",
        ) from error


@router.delete(
    "/searches/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="cancel_analysis_search",
)
async def cancel_analysis_search(
    session_id: UUID,
    authenticated: Annotated[AuthenticatedSession, Depends(require_csrf_session)],
    service: Annotated[AnalysisService, Depends(analysis_service)],
) -> Response:
    try:
        await service.cancel_search(
            authenticated.scope,
            session_id,
            datetime.now(UTC),
        )
    except AnalysisNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Analysis search not found.",
        ) from error
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Document analysis could not be cancelled.",
        ) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/searches/{session_id}/retry",
    response_model=AnalysisSessionResponse,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="retry_analysis_search",
)
async def retry_analysis_search(
    session_id: UUID,
    authenticated: Annotated[AuthenticatedSession, Depends(require_csrf_session)],
    service: Annotated[AnalysisService, Depends(analysis_service)],
) -> AnalysisSessionResponse:
    try:
        return await service.retry_search(
            authenticated.scope,
            session_id,
            datetime.now(UTC),
        )
    except AnalysisNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Analysis search not found.",
        ) from error
    except AnalysisRetryConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Analysis search cannot be retried.",
        ) from error
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Document analysis could not be retried.",
        ) from error


@router.get(
    "/analyses/{candidate_id}/preview",
    operation_id="get_analysis_preview",
)
async def get_analysis_preview(
    candidate_id: UUID,
    authenticated: Annotated[
        AuthenticatedSession,
        Depends(require_authenticated_session),
    ],
    service: Annotated[AnalysisService, Depends(analysis_service)],
) -> Response:
    try:
        preview = await service.preview_stream(
            authenticated.scope,
            candidate_id,
            datetime.now(UTC),
        )
    except (AnalysisNotFoundError, AnalysisPreviewUnavailableError) as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Analysis preview not found.",
        ) from error
    except AnalysisExpiredError as error:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Analysis preview expired.",
        ) from error
    except ArtifactStorageError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Analysis preview is temporarily unavailable.",
        ) from error
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Analysis preview is unavailable.",
        ) from error
    return StreamingResponse(
        preview.body,
        media_type=preview.media_type,
        headers={
            "Cache-Control": "private, no-store",
            "Content-Length": str(preview.size_bytes),
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        },
    )
