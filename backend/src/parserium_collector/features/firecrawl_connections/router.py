from datetime import UTC, datetime
from typing import Annotated, NoReturn, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from parserium_collector.features.firecrawl_connections.errors import (
    ConnectionDefaultConflictError,
    ConnectionNameConflictError,
    ConnectionNotFoundError,
    ConnectionPermissionError,
    ConnectionUnavailableError,
    CredentialUnavailableError,
    RemoteEndpointRejectedError,
)
from parserium_collector.features.firecrawl_connections.models import (
    ConnectionListResponse,
    ConnectionSummary,
    CreateConnectionRequest,
    FirecrawlConnectionRecord,
    ReplaceCredentialRequest,
    UpdateConnectionRequest,
)
from parserium_collector.features.firecrawl_connections.service import (
    FirecrawlConnectionService,
)
from parserium_collector.features.session.dependencies import (
    require_authenticated_session,
    require_csrf_session,
)
from parserium_collector.features.session.models import AuthenticatedSession
from parserium_collector.settings import DeploymentMode, Settings


def require_hosted_mode(request: Request) -> None:
    settings = cast(Settings, request.app.state.settings)
    if settings.deployment_mode is not DeploymentMode.HOSTED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")


router = APIRouter(
    prefix="/api/v1/firecrawl/connections",
    tags=["firecrawl-connections"],
    dependencies=[Depends(require_hosted_mode)],
)


def connection_service(request: Request) -> FirecrawlConnectionService:
    service = cast(
        FirecrawlConnectionService | None,
        getattr(request.app.state, "firecrawl_connection_service", None),
    )
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "connection_service_unavailable",
                "message": "Firecrawl connection management is temporarily unavailable.",
            },
        )
    return service


def _summary(record: FirecrawlConnectionRecord) -> ConnectionSummary:
    return ConnectionSummary.from_record(record, include_endpoint=True)


def _raise_domain_error(error: Exception) -> NoReturn:
    if isinstance(error, ConnectionNotFoundError):
        http_status = status.HTTP_404_NOT_FOUND
        code = "connection_not_found"
        message = "Firecrawl connection not found."
    elif isinstance(error, ConnectionPermissionError):
        http_status = status.HTTP_403_FORBIDDEN
        code = "connection_permission_denied"
        message = "Workspace owner access is required."
    elif isinstance(error, ConnectionNameConflictError):
        http_status = status.HTTP_409_CONFLICT
        code = "connection_name_conflict"
        message = "A Firecrawl connection already uses that name."
    elif isinstance(error, ConnectionDefaultConflictError):
        http_status = status.HTTP_409_CONFLICT
        code = "connection_default_conflict"
        message = "The default Firecrawl connection could not be changed."
    elif isinstance(error, RemoteEndpointRejectedError):
        http_status = status.HTTP_422_UNPROCESSABLE_CONTENT
        code = "connection_endpoint_rejected"
        message = "The remote Firecrawl endpoint is not allowed."
    elif isinstance(error, ValueError):
        http_status = status.HTTP_422_UNPROCESSABLE_CONTENT
        code = "connection_invalid"
        message = "The Firecrawl connection request is invalid."
    elif isinstance(error, ConnectionUnavailableError):
        http_status = status.HTTP_503_SERVICE_UNAVAILABLE
        code = "connection_unavailable"
        message = "The Firecrawl connection is temporarily unavailable."
    elif isinstance(error, CredentialUnavailableError):
        http_status = status.HTTP_503_SERVICE_UNAVAILABLE
        code = "credential_unavailable"
        message = "The Firecrawl credential is temporarily unavailable."
    else:
        raise error
    raise HTTPException(
        status_code=http_status,
        detail={"code": code, "message": message},
    ) from error


@router.get(
    "",
    response_model=ConnectionListResponse,
    operation_id="list_firecrawl_connections",
)
async def list_connections(
    authenticated: Annotated[
        AuthenticatedSession,
        Depends(require_authenticated_session),
    ],
    service: Annotated[FirecrawlConnectionService, Depends(connection_service)],
) -> ConnectionListResponse:
    return await service.list_connections(authenticated.scope)


@router.post(
    "",
    response_model=ConnectionSummary,
    status_code=status.HTTP_201_CREATED,
    operation_id="create_firecrawl_connection",
)
async def create_connection(
    payload: CreateConnectionRequest,
    authenticated: Annotated[AuthenticatedSession, Depends(require_csrf_session)],
    service: Annotated[FirecrawlConnectionService, Depends(connection_service)],
) -> ConnectionSummary:
    try:
        record = await service.create_connection(authenticated.scope, payload, datetime.now(UTC))
    except Exception as error:
        _raise_domain_error(error)
    return _summary(record)


@router.patch(
    "/{connection_id}",
    response_model=ConnectionSummary,
    operation_id="update_firecrawl_connection",
)
async def update_connection(
    connection_id: UUID,
    payload: UpdateConnectionRequest,
    authenticated: Annotated[AuthenticatedSession, Depends(require_csrf_session)],
    service: Annotated[FirecrawlConnectionService, Depends(connection_service)],
) -> ConnectionSummary:
    try:
        record = await service.update_connection(
            authenticated.scope,
            connection_id,
            payload,
            datetime.now(UTC),
        )
    except Exception as error:
        _raise_domain_error(error)
    return _summary(record)


@router.post(
    "/{connection_id}/test",
    response_model=ConnectionSummary,
    operation_id="test_firecrawl_connection",
)
async def test_connection(
    connection_id: UUID,
    authenticated: Annotated[AuthenticatedSession, Depends(require_csrf_session)],
    service: Annotated[FirecrawlConnectionService, Depends(connection_service)],
) -> ConnectionSummary:
    try:
        record = await service.test_connection(
            authenticated.scope,
            connection_id,
            datetime.now(UTC),
        )
    except Exception as error:
        _raise_domain_error(error)
    return _summary(record)


@router.put(
    "/{connection_id}/credential",
    response_model=ConnectionSummary,
    operation_id="replace_firecrawl_connection_credential",
)
async def replace_credential(
    connection_id: UUID,
    payload: ReplaceCredentialRequest,
    authenticated: Annotated[AuthenticatedSession, Depends(require_csrf_session)],
    service: Annotated[FirecrawlConnectionService, Depends(connection_service)],
) -> ConnectionSummary:
    try:
        record = await service.replace_credential(
            authenticated.scope,
            connection_id,
            payload.credential,
            datetime.now(UTC),
        )
    except Exception as error:
        _raise_domain_error(error)
    return _summary(record)


@router.delete(
    "/{connection_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="delete_firecrawl_connection",
)
async def delete_connection(
    connection_id: UUID,
    authenticated: Annotated[AuthenticatedSession, Depends(require_csrf_session)],
    service: Annotated[FirecrawlConnectionService, Depends(connection_service)],
) -> Response:
    try:
        await service.delete_connection(
            authenticated.scope,
            connection_id,
            datetime.now(UTC),
        )
    except Exception as error:
        _raise_domain_error(error)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
