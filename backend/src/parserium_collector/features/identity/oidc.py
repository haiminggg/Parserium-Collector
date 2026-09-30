import ssl
from collections.abc import Mapping
from typing import Any, cast

from authlib.integrations.starlette_client import (  # type: ignore[import-untyped]
    OAuth,
    OAuthError,
    StarletteOAuth2App,
)
from httpx import HTTPError
from joserfc.errors import JoseError
from starlette.requests import Request
from starlette.responses import RedirectResponse

from parserium_collector.features.identity.models import (
    OidcAuthenticationFailed,
    OidcClaims,
)
from parserium_collector.settings import Settings


class OidcAdapter:
    def __init__(self, settings: Settings) -> None:
        if settings.oidc_issuer is None or settings.oidc_client_id is None:
            raise ValueError("OpenID Connect settings are incomplete.")
        self._issuer = str(settings.oidc_issuer).removesuffix("/")
        transport_verification: bool | ssl.SSLContext = True
        if settings.oidc_ca_bundle_file is not None:
            transport_verification = ssl.create_default_context(
                cafile=str(settings.oidc_ca_bundle_file)
            )
        oauth = OAuth()
        client = oauth.register(
            name="parserium_oidc",
            client_id=settings.oidc_client_id,
            client_secret=settings.oidc_client_secret(),
            server_metadata_url=(f"{self._issuer}/.well-known/openid-configuration"),
            client_kwargs={
                "scope": "openid profile email",
                "code_challenge_method": "S256",
                "verify": transport_verification,
            },
        )
        if client is None:
            raise ValueError("OpenID Connect client registration failed.")
        self._client = cast(StarletteOAuth2App, client)

    async def authorize_redirect(
        self,
        request: Request,
        redirect_uri: str,
    ) -> RedirectResponse:
        try:
            response = await self._client.authorize_redirect(request, redirect_uri)
        except (HTTPError, JoseError, OAuthError, KeyError, TypeError, ValueError, RuntimeError):
            raise OidcAuthenticationFailed from None
        return cast(RedirectResponse, response)

    async def authorize_access_token(self, request: Request) -> OidcClaims:
        try:
            token = await self._client.authorize_access_token(request)
            userinfo = token.get("userinfo")
            return self._validated_claims(userinfo)
        except (HTTPError, JoseError, OAuthError, KeyError, TypeError, ValueError, RuntimeError):
            raise OidcAuthenticationFailed from None

    def _validated_claims(self, value: object) -> OidcClaims:
        if not isinstance(value, Mapping):
            raise OidcAuthenticationFailed
        issuer = _required_string(value, "iss").removesuffix("/")
        if issuer != self._issuer:
            raise OidcAuthenticationFailed
        subject = _required_string(value, "sub")
        email = _required_string(value, "email")
        if value.get("email_verified") is not True:
            raise OidcAuthenticationFailed
        display_name_value = value.get("name")
        if display_name_value is not None and not isinstance(display_name_value, str):
            raise OidcAuthenticationFailed
        display_name = (
            display_name_value.strip() or None if isinstance(display_name_value, str) else None
        )
        return OidcClaims(
            issuer=issuer,
            subject=subject,
            email=email,
            email_verified=True,
            display_name=display_name,
        )


def _required_string(values: Mapping[str, Any], name: str) -> str:
    value = values.get(name)
    if not isinstance(value, str) or not value.strip():
        raise OidcAuthenticationFailed
    return value.strip()
