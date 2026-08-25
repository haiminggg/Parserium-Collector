from datetime import UTC, datetime
from typing import Annotated, cast

from fastapi import APIRouter, Cookie, Header, HTTPException, Request, Response, status

from parserium_collector.features.session.dependencies import SESSION_COOKIE
from parserium_collector.features.session.errors import (
    InvalidCsrfToken,
    InvalidPairingCode,
    InvalidSession,
)
from parserium_collector.features.session.models import (
    AuthenticatedSession,
    IssuedSession,
    PairingRequest,
    PublicSessionStatus,
    SessionResponse,
)
from parserium_collector.features.session.service import SessionService
from parserium_collector.settings import Settings

router = APIRouter(prefix="/api/v1/session", tags=["session"])


def _service(request: Request) -> SessionService:
    return cast(SessionService, request.app.state.session_service)


def _settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def _response(authenticated: AuthenticatedSession | IssuedSession) -> SessionResponse:
    return SessionResponse(
        csrf_token=authenticated.csrf_token,
        idle_expires_at=authenticated.idle_expires_at,
    )


@router.get("/status", response_model=PublicSessionStatus | SessionResponse)
async def get_session_status(
    request: Request,
    session_token: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
) -> PublicSessionStatus | SessionResponse:
    if session_token is None:
        return PublicSessionStatus(status="pairing_required")
    try:
        authenticated = await _service(request).authenticate(session_token, datetime.now(UTC))
    except InvalidSession:
        return PublicSessionStatus(status="pairing_required")
    return _response(authenticated)


@router.post("/pair", response_model=SessionResponse)
async def pair_session(
    payload: PairingRequest,
    request: Request,
    response: Response,
) -> SessionResponse:
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
        authenticated = await _service(request).authenticate(session_token, datetime.now(UTC))
    except InvalidSession as error:
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
        await _service(request).logout(session_token, csrf, datetime.now(UTC))
    except InvalidSession as error:
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
    response.delete_cookie(
        key=SESSION_COOKIE,
        httponly=True,
        secure=_settings(request).session_cookie_secure,
        samesite="strict",
        path="/",
    )
    return response
