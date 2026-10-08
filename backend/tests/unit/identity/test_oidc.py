import base64
import ssl
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
import pytest
import respx
from joserfc import jwt
from joserfc.jwk import RSAKey
from starlette.requests import Request

from parserium_collector.features.identity.models import OidcAuthenticationFailed
from parserium_collector.features.identity.oidc import OidcAdapter
from parserium_collector.settings import DeploymentMode, Settings

ISSUER = "https://identity.parserium.test"
METADATA_URL = f"{ISSUER}/.well-known/openid-configuration"
AUTHORIZATION_URL = f"{ISSUER}/authorize"
TOKEN_URL = f"{ISSUER}/token"
JWKS_URL = f"{ISSUER}/jwks"
CLIENT_ID = "parserium-client"
REDIRECT_URI = "https://app.parserium.test/api/v1/auth/callback"


def _settings(tmp_path: Path) -> Settings:
    session_secret = tmp_path / "session-secret"
    session_secret.write_bytes(b"s" * 32)
    client_secret = tmp_path / "oidc-secret"
    client_secret.write_bytes(b"c" * 32)
    return Settings(
        deployment_mode=DeploymentMode.HOSTED,
        storage_backend="s3",
        storage_endpoint="https://minio.parserium.test",
        storage_region="us-east-1",
        storage_bucket="parserium-artifacts",
        public_origin="https://app.parserium.test",
        session_cookie_secure=True,
        session_signing_secret_file=session_secret,
        oidc_issuer=ISSUER,
        oidc_client_id=CLIENT_ID,
        oidc_client_secret_file=client_secret,
        oidc_redirect_uri=REDIRECT_URI,
    )


def test_custom_ca_bundle_is_passed_as_a_verified_ssl_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ca_bundle = tmp_path / "ca.pem"
    ca_bundle.write_text("verification CA", encoding="utf-8")
    settings = _settings(tmp_path).model_copy(update={"oidc_ca_bundle_file": ca_bundle})
    verified_context = ssl.create_default_context()
    captured: list[str] = []

    def create_context(*, cafile: str) -> ssl.SSLContext:
        captured.append(cafile)
        return verified_context

    monkeypatch.setattr(ssl, "create_default_context", create_context)

    adapter = OidcAdapter(settings)

    assert captured == [str(ca_bundle)]
    assert adapter._client.client_kwargs["verify"] is verified_context


def _metadata() -> dict[str, Any]:
    return {
        "issuer": ISSUER,
        "authorization_endpoint": AUTHORIZATION_URL,
        "token_endpoint": TOKEN_URL,
        "jwks_uri": JWKS_URL,
        "response_types_supported": ["code"],
        "subject_types_supported": ["public"],
        "id_token_signing_alg_values_supported": ["RS256"],
        "token_endpoint_auth_methods_supported": ["client_secret_basic"],
    }


def _request(
    *,
    session: dict[str, Any],
    query: Mapping[str, str] | None = None,
) -> Request:
    query_string = urlencode(query or {}).encode("ascii")
    return Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "https",
            "path": "/api/v1/auth/callback",
            "raw_path": b"/api/v1/auth/callback",
            "query_string": query_string,
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "server": ("app.parserium.test", 443),
            "session": session,
        }
    )


async def _begin_login(adapter: OidcAdapter) -> tuple[dict[str, Any], dict[str, str]]:
    session: dict[str, Any] = {}
    response = await adapter.authorize_redirect(
        _request(session=session),
        REDIRECT_URI,
    )
    parameters = {
        key: values[0]
        for key, values in parse_qs(urlparse(response.headers["location"]).query).items()
    }
    return session, parameters


def _signed_id_token(
    key: RSAKey,
    *,
    nonce: str,
    overrides: Mapping[str, Any] | None = None,
) -> str:
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": ISSUER,
        "sub": "subject-123",
        "aud": CLIENT_ID,
        "iat": now,
        "exp": now + 600,
        "nonce": nonce,
        "email": "Owner@Parserium.Test",
        "email_verified": True,
        "name": "Parserium Owner",
    }
    for name, value in (overrides or {}).items():
        if value is None:
            claims.pop(name, None)
        else:
            claims[name] = value
    return jwt.encode({"alg": "RS256", "kid": key.kid}, claims, key)


@pytest.mark.asyncio
async def test_authorization_uses_discovery_nonce_state_and_s256_pkce(tmp_path: Path) -> None:
    adapter = OidcAdapter(_settings(tmp_path))
    with respx.mock(assert_all_called=True) as mocked:
        discovery = mocked.get(METADATA_URL).mock(
            return_value=httpx.Response(200, json=_metadata())
        )
        session, parameters = await _begin_login(adapter)

    assert discovery.called
    assert parameters["response_type"] == "code"
    assert set(parameters["scope"].split()) == {"openid", "profile", "email"}
    assert parameters["state"]
    assert parameters["nonce"]
    assert parameters["code_challenge_method"] == "S256"
    assert parameters["code_challenge"]
    stored_flow = session[f"_state_parserium_oidc_{parameters['state']}"]["data"]
    assert stored_flow["nonce"] == parameters["nonce"]
    assert stored_flow["code_verifier"]


@pytest.mark.asyncio
async def test_callback_returns_only_validated_identity_claims(tmp_path: Path) -> None:
    key = RSAKey.generate_key(auto_kid=True)
    adapter = OidcAdapter(_settings(tmp_path))
    with respx.mock(assert_all_called=True) as mocked:
        mocked.get(METADATA_URL).mock(return_value=httpx.Response(200, json=_metadata()))
        session, parameters = await _begin_login(adapter)
        token = _signed_id_token(key, nonce=parameters["nonce"])
        token_request = mocked.post(TOKEN_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "access_token": "provider-access-secret",
                    "refresh_token": "provider-refresh-secret",
                    "token_type": "Bearer",
                    "id_token": token,
                },
            )
        )
        mocked.get(JWKS_URL).mock(
            return_value=httpx.Response(200, json={"keys": [key.as_dict(private=False)]})
        )

        claims = await adapter.authorize_access_token(
            _request(
                session=session,
                query={"code": "test-code", "state": parameters["state"]},
            )
        )

    assert token_request.called
    expected_basic = base64.b64encode(f"{CLIENT_ID}:{'c' * 32}".encode()).decode()
    assert token_request.calls.last.request.headers["Authorization"] == (f"Basic {expected_basic}")
    assert claims.issuer == ISSUER
    assert claims.subject == "subject-123"
    assert claims.email == "Owner@Parserium.Test"
    assert claims.email_verified is True
    assert claims.display_name == "Parserium Owner"
    assert "provider-access-secret" not in repr(claims)
    assert "provider-refresh-secret" not in repr(claims)
    assert not hasattr(claims, "id_token")


@pytest.mark.parametrize(
    "overrides",
    [
        {"iss": "https://other-issuer.test"},
        {"sub": None},
        {"email": None},
        {"email_verified": False},
        {"nonce": "wrong-nonce"},
    ],
)
@pytest.mark.asyncio
async def test_callback_rejects_invalid_identity_claims(
    tmp_path: Path,
    overrides: Mapping[str, Any],
) -> None:
    key = RSAKey.generate_key(auto_kid=True)
    adapter = OidcAdapter(_settings(tmp_path))
    with respx.mock(assert_all_called=True) as mocked:
        mocked.get(METADATA_URL).mock(return_value=httpx.Response(200, json=_metadata()))
        session, parameters = await _begin_login(adapter)
        token = _signed_id_token(key, nonce=parameters["nonce"], overrides=overrides)
        mocked.post(TOKEN_URL).mock(
            return_value=httpx.Response(
                200,
                json={"access_token": "access", "token_type": "Bearer", "id_token": token},
            )
        )
        mocked.get(JWKS_URL).mock(
            return_value=httpx.Response(200, json={"keys": [key.as_dict(private=False)]})
        )

        with pytest.raises(OidcAuthenticationFailed):
            await adapter.authorize_access_token(
                _request(
                    session=session,
                    query={"code": "test-code", "state": parameters["state"]},
                )
            )


@pytest.mark.asyncio
async def test_callback_rejects_invalid_signature(tmp_path: Path) -> None:
    trusted_key = RSAKey.generate_key(auto_kid=True)
    signing_key = RSAKey.generate_key(parameters={"kid": trusted_key.kid})
    adapter = OidcAdapter(_settings(tmp_path))
    with respx.mock(assert_all_called=True) as mocked:
        mocked.get(METADATA_URL).mock(return_value=httpx.Response(200, json=_metadata()))
        session, parameters = await _begin_login(adapter)
        mocked.post(TOKEN_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "access_token": "access",
                    "token_type": "Bearer",
                    "id_token": _signed_id_token(signing_key, nonce=parameters["nonce"]),
                },
            )
        )
        mocked.get(JWKS_URL).mock(
            return_value=httpx.Response(
                200,
                json={"keys": [trusted_key.as_dict(private=False)]},
            )
        )

        with pytest.raises(OidcAuthenticationFailed):
            await adapter.authorize_access_token(
                _request(
                    session=session,
                    query={"code": "test-code", "state": parameters["state"]},
                )
            )


@pytest.mark.asyncio
async def test_transport_and_provider_details_are_not_exposed(tmp_path: Path) -> None:
    adapter = OidcAdapter(_settings(tmp_path))
    with respx.mock(assert_all_called=True) as mocked:
        mocked.get(METADATA_URL).mock(side_effect=httpx.ConnectError("private network detail"))
        with pytest.raises(OidcAuthenticationFailed) as caught:
            await _begin_login(adapter)
    assert str(caught.value) == "OpenID Connect authentication failed."
    assert "private network detail" not in str(caught.value)


@pytest.mark.asyncio
async def test_provider_callback_error_is_mapped_without_description(tmp_path: Path) -> None:
    adapter = OidcAdapter(_settings(tmp_path))
    with pytest.raises(OidcAuthenticationFailed) as caught:
        await adapter.authorize_access_token(
            _request(
                session={},
                query={
                    "error": "access_denied",
                    "error_description": "private provider detail",
                },
            )
        )
    assert str(caught.value) == "OpenID Connect authentication failed."
    assert "private provider detail" not in str(caught.value)
