import logging
import re
from datetime import UTC, datetime
from typing import Annotated, cast

from fastapi import APIRouter, Form, HTTPException, Request, status
from starlette.responses import RedirectResponse

from parserium_collector.features.identity.models import (
    HostedIdentityRejected,
    InvitationRejected,
    InvitationRequired,
    OidcAuthenticationFailed,
)
from parserium_collector.features.identity.oidc import OidcAdapter
from parserium_collector.features.identity.service import IdentityService
from parserium_collector.features.session.crypto import keyed_digest
from parserium_collector.features.session.dependencies import SESSION_COOKIE
from parserium_collector.settings import DeploymentMode, Settings

router = APIRouter(prefix="/api/v1/auth", tags=["identity"])
INVITATION_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")
LOGGER = logging.getLogger(__name__)


def _settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def _oidc(request: Request) -> OidcAdapter:
    return cast(OidcAdapter, request.app.state.oidc_adapter)


def _identity(request: Request) -> IdentityService:
    return cast(IdentityService, request.app.state.identity_service)


@router.post("/login")
async def login(
    request: Request,
    invite: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    settings = _settings(request)
    if settings.deployment_mode is not DeploymentMode.HOSTED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    if invite is not None:
        if INVITATION_PATTERN.fullmatch(invite) is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="The invitation is invalid.",
            )
        request.session["invitation_digest"] = keyed_digest(
            settings.session_signing_secret(),
            "invitation",
            invite,
        )
    else:
        request.session.pop("invitation_digest", None)
    if settings.oidc_redirect_uri is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
    return await _oidc(request).authorize_redirect(
        request,
        str(settings.oidc_redirect_uri),
    )


@router.get("/callback")
async def callback(request: Request) -> RedirectResponse:
    settings = _settings(request)
    if settings.deployment_mode is not DeploymentMode.HOSTED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    invitation_digest = request.session.get("invitation_digest")
    if invitation_digest is not None and (
        not isinstance(invitation_digest, str) or len(invitation_digest) != 64
    ):
        request.session.clear()
        return RedirectResponse("/?auth=failed", status_code=status.HTTP_303_SEE_OTHER)
    try:
        claims = await _oidc(request).authorize_access_token(request)
        issued = await _identity(request).complete_login(
            claims,
            invitation_digest=invitation_digest,
            now=datetime.now(UTC),
        )
    except (
        HostedIdentityRejected,
        InvitationRejected,
        InvitationRequired,
        OidcAuthenticationFailed,
    ) as error:
        LOGGER.warning(
            "Hosted authentication callback rejected: %s.",
            type(error).__name__,
        )
        request.session.clear()
        return RedirectResponse("/?auth=failed", status_code=status.HTTP_303_SEE_OTHER)
    request.session.clear()
    response = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        key=SESSION_COOKIE,
        value=issued.token,
        max_age=settings.session_idle_seconds,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )
    return response
