from datetime import UTC, datetime
from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, Request, status

from parserium_collector.adapters.firecrawl.errors import FirecrawlAdapterError
from parserium_collector.features.discovery.models import (
    DocumentDiscoveryRequest,
    DocumentDiscoveryResponse,
)
from parserium_collector.features.discovery.scoped import ScopedDiscoveryService
from parserium_collector.features.firecrawl_connections.errors import (
    ConnectionNotFoundError,
    ConnectionUnavailableError,
    CredentialUnavailableError,
)
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
    service = cast(
        ScopedDiscoveryService | None,
        getattr(request.app.state, "discovery_service", None),
    )
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Document discovery is not configured.",
        )
    try:
        discovered = await service.search(
            authenticated.scope,
            payload,
            datetime.now(UTC),
        )
        return discovered.response
    except ConnectionNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Firecrawl connection not found.",
        ) from error
    except (ConnectionUnavailableError, CredentialUnavailableError) as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The selected Firecrawl connection is unavailable.",
        ) from error
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="The Firecrawl connection selection is invalid.",
        ) from error
    except FirecrawlAdapterError as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Document discovery is unavailable.",
        ) from error
