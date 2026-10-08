import hmac
from datetime import UTC, datetime
from typing import Annotated, cast

from fastapi import APIRouter, Cookie, Header, HTTPException, Request, Response, status

from parserium_collector.features.identity.models import (
    HostedSessionNotFound,
    WorkspaceMembershipNotFound,
)
from parserium_collector.features.identity.service import IdentityService
from parserium_collector.features.session.dependencies import SESSION_COOKIE
from parserium_collector.features.session.errors import (
    InvalidCsrfToken,
    InvalidPairingCode,
    InvalidSession,
)
from parserium_collector.features.session.models import (
    AuthenticatedSession,
    IssuedSession,
    LoginRequiredSession,
    PairingRequest,
    PublicSessionStatus,
    SessionResponse,
    UserSummary,
    WorkspaceSummary,
    WorkspaceSwitchRequest,
)
from parserium_collector.features.session.service import SessionService
from parserium_collector.settings import DeploymentMode, Settings

router = APIRouter(prefix="/api/v1/session", tags=["session"])


def _service(request: Request) -> SessionService:
    return cast(SessionService, request.app.state.session_service)


def _settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def _identity_service(request: Request) -> IdentityService:
    return cast(IdentityService, request.app.state.identity_service)


def _response(authenticated: AuthenticatedSession | IssuedSession) -> SessionResponse:
    user_id = authenticated.scope.user_id
    return SessionResponse(
        csrf_token=authenticated.csrf_token,
        idle_expires_at=authenticated.idle_expires_at,
        authentication_mode=authenticated.authentication_mode,
        user=(
            UserSummary(
                id=user_id,
                email=authenticated.email or "",
                display_name=authenticated.display_name,
            )
            if user_id is not None and authenticated.email is not None
            else None
        ),
        workspace=WorkspaceSummary(
            id=authenticated.scope.workspace_id,
            name=authenticated.workspace_name,
            role=authenticated.scope.role,
        ),
        workspaces=authenticated.workspaces,
    )


async def _authenticate(
    request: Request,
    session_token: str,
) -> AuthenticatedSession:
    if _settings(request).deployment_mode is DeploymentMode.HOSTED:
        return await _identity_service(request).authenticate(
            session_token,
            datetime.now(UTC),
        )
    return await _service(request).authenticate(session_token, datetime.now(UTC))


@router.get(
    "/status",
    response_model=PublicSessionStatus | LoginRequiredSession | SessionResponse,
)
async def get_session_status(
    request: Request,
    session_token: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
) -> PublicSessionStatus | LoginRequiredSession | SessionResponse:
    if session_token is None:
        if _settings(request).deployment_mode is DeploymentMode.HOSTED:
            return LoginRequiredSession(
                provider_label=_settings(request).oidc_provider_label,
            )
        return PublicSessionStatus(status="pairing_required")
    try:
        authenticated = await _authenticate(request, session_token)
    except (HostedSessionNotFound, InvalidSession):
        if _settings(request).deployment_mode is DeploymentMode.HOSTED:
            return LoginRequiredSession(
                provider_label=_settings(request).oidc_provider_label,
            )
        return PublicSessionStatus(status="pairing_required")
    return _response(authenticated)


@router.post("/pair", response_model=SessionResponse)
async def pair_session(
    payload: PairingRequest,
    request: Request,
    response: Response,
) -> SessionResponse:
    if _settings(request).deployment_mode is DeploymentMode.HOSTED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    try:
        issued = await _service(request).pair(payload.code, datetime.now(UTC))
    except InvalidPairingCode as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired pairing code.",
        ) from error
    settings = _settings(request)
    response.set_cookie(
        key=SESSION_COOKIE,
        value=issued.token,
        max_age=settings.session_idle_seconds,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="strict",
        path="/",
    )
    return _response(issued)


@router.get("", response_model=SessionResponse)
async def get_session(
    request: Request,
    session_token: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
) -> SessionResponse:
    if session_token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
        )
    try:
        authenticated = await _authenticate(request, session_token)
    except (HostedSessionNotFound, InvalidSession) as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
        ) from error
    return _response(authenticated)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout_session(
    request: Request,
    session_token: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    csrf: Annotated[str | None, Header(alias="X-Parserium-CSRF")] = None,
) -> Response:
    if session_token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
        )
    if csrf is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token.")
    try:
        if _settings(request).deployment_mode is DeploymentMode.HOSTED:
            await _identity_service(request).logout(session_token, csrf, datetime.now(UTC))
        else:
            await _service(request).logout(session_token, csrf, datetime.now(UTC))
    except (HostedSessionNotFound, InvalidSession) as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
        ) from error
    except InvalidCsrfToken as error:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid CSRF token.",
        ) from error
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    is_hosted = _settings(request).deployment_mode is DeploymentMode.HOSTED
    response.delete_cookie(
        key=SESSION_COOKIE,
        httponly=True,
        secure=_settings(request).session_cookie_secure,
        samesite="lax" if is_hosted else "strict",
        path="/",
    )
    return response


@router.post("/workspace", response_model=SessionResponse)
async def switch_workspace(
    payload: WorkspaceSwitchRequest,
    request: Request,
    session_token: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    csrf: Annotated[str | None, Header(alias="X-Parserium-CSRF")] = None,
) -> SessionResponse:
    if _settings(request).deployment_mode is not DeploymentMode.HOSTED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Workspace switching requires hosted authentication.",
        )
    if session_token is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    try:
        authenticated = await _identity_service(request).authenticate(
            session_token,
            datetime.now(UTC),
        )
    except HostedSessionNotFound as error:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED) from error
    if csrf is None or not hmac.compare_digest(authenticated.csrf_token, csrf):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token.")
    try:
        changed = await _identity_service(request).switch_workspace(
            session_token,
            payload.workspace_id,
            datetime.now(UTC),
        )
    except WorkspaceMembershipNotFound as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from error
    return _response(changed)
