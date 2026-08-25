from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, Request, status

from parserium_collector.adapters.firecrawl.errors import FirecrawlAdapterError
from parserium_collector.features.discovery.models import (
    DocumentDiscoveryRequest,
    DocumentDiscoveryResponse,
)
from parserium_collector.features.discovery.service import DiscoveryService
from parserium_collector.features.session.dependencies import require_csrf_session
from parserium_collector.features.session.models import AuthenticatedSession

router = APIRouter(prefix="/api/v1/discovery", tags=["discovery"])


@router.post(
    "/search",
    response_model=DocumentDiscoveryResponse,
    operation_id="search_documents",
)
async def search_documents(
    payload: DocumentDiscoveryRequest,
    request: Request,
    authenticated: Annotated[AuthenticatedSession, Depends(require_csrf_session)],
) -> DocumentDiscoveryResponse:
    del authenticated
    service = cast(DiscoveryService | None, getattr(request.app.state, "discovery_service", None))
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Document discovery is not configured.",
        )
    try:
        return await service.search(payload)
    except FirecrawlAdapterError as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Document discovery is unavailable.",
        ) from error
