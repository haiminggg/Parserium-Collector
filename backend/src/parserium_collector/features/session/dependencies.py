import hmac
from datetime import UTC, datetime
from typing import Annotated, cast

from fastapi import Cookie, Depends, Header, HTTPException, Request, status

from parserium_collector.features.session.errors import InvalidSession
from parserium_collector.features.session.models import AuthenticatedSession
from parserium_collector.features.session.service import SessionService

SESSION_COOKIE = "parserium_session"


async def require_authenticated_session(
    request: Request,
    session_token: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
) -> AuthenticatedSession:
    if session_token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
        )
    service = cast(SessionService, request.app.state.session_service)
    try:
        return await service.authenticate(session_token, datetime.now(UTC))
    except InvalidSession as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
        ) from error


async def require_csrf_session(
    authenticated: Annotated[AuthenticatedSession, Depends(require_authenticated_session)],
    csrf: Annotated[str | None, Header(alias="X-Parserium-CSRF")] = None,
) -> AuthenticatedSession:
    if csrf is None or not hmac.compare_digest(authenticated.csrf_token, csrf):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token.")
    return authenticated
