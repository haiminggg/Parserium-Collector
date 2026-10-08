import base64
import hashlib
import hmac
import html
import json
import os
import secrets
import ssl
import threading
import time
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar
from urllib.parse import parse_qs, urlencode, urlsplit

from joserfc import jwt
from joserfc.jwk import RSAKey


@dataclass(frozen=True)
class ProviderConfiguration:
    issuer: str
    client_id: str
    client_secret: bytes
    redirect_uri: str
    certificate_file: Path
    certificate_key_file: Path
    signing_key_file: Path


@dataclass(frozen=True)
class Identity:
    subject: str
    email: str
    name: str


@dataclass(frozen=True)
class AuthorizationCode:
    client_id: str
    redirect_uri: str
    state: str
    nonce: str
    code_challenge: str
    identity: Identity
    expires_at: float


IDENTITIES = {
    "user-a": Identity(
        subject="verification-user-a",
        email="owner-a@example.com",
        name="Workspace A Owner",
    ),
    "user-b": Identity(
        subject="verification-user-b",
        email="owner-b@example.com",
        name="Workspace B Owner",
    ),
}


class VerificationOidcHandler(BaseHTTPRequestHandler):
    configuration: ClassVar[ProviderConfiguration]
    signing_key: ClassVar[RSAKey]
    authorization_codes: ClassVar[dict[str, AuthorizationCode]] = {}
    code_lock: ClassVar[threading.Lock] = threading.Lock()

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_GET(self) -> None:
        request_url = urlsplit(self.path)
        if request_url.path == "/health":
            self._json(HTTPStatus.OK, {"status": "ok"})
            return
        if request_url.path == "/.well-known/openid-configuration":
            issuer = self.configuration.issuer
            self._json(
                HTTPStatus.OK,
                {
                    "issuer": issuer,
                    "authorization_endpoint": f"{issuer}/authorize",
                    "token_endpoint": f"{issuer}/token",
                    "jwks_uri": f"{issuer}/jwks",
                    "response_types_supported": ["code"],
                    "subject_types_supported": ["public"],
                    "id_token_signing_alg_values_supported": ["RS256"],
                    "token_endpoint_auth_methods_supported": ["client_secret_basic"],
                    "scopes_supported": ["openid", "profile", "email"],
                    "code_challenge_methods_supported": ["S256"],
                },
            )
            return
        if request_url.path == "/jwks":
            public_key = self.signing_key.as_dict(private=False)
            public_key.update({"use": "sig", "alg": "RS256"})
            self._json(HTTPStatus.OK, {"keys": [public_key]})
            return
        if request_url.path == "/authorize":
            parameters = self._single_parameters(parse_qs(request_url.query))
            if not self._valid_authorization_request(parameters):
                self._text(HTTPStatus.BAD_REQUEST, "Invalid authorization request.")
                return
            self._authorization_page(parameters)
            return
        self._text(HTTPStatus.NOT_FOUND, "Not found.")

    def do_POST(self) -> None:
        request_path = urlsplit(self.path).path
        if request_path == "/authorize":
            self._complete_authorization(self._read_form())
            return
        if request_path == "/token":
            self._exchange_token(self._read_form())
            return
        self._text(HTTPStatus.NOT_FOUND, "Not found.")

    def _authorization_page(self, parameters: dict[str, str]) -> None:
        hidden_fields = "".join(
            f'<input type="hidden" name="{html.escape(name, quote=True)}" '
            f'value="{html.escape(value, quote=True)}">'
            for name, value in parameters.items()
        )
        page = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Parserium verification identity</title>
  <style>
    body {{ margin: 0; font: 16px system-ui, sans-serif; color: #171714; background: #f8f7f2; }}
    main {{ width: min(92vw, 34rem); margin: 12vh auto; padding: 2rem; border: 1px solid #d8d5cc; background: #fff; }}
    h1 {{ margin-top: 0; font-size: 2rem; }}
    p {{ color: #625f57; line-height: 1.5; }}
    button {{ width: 100%; margin-top: .75rem; padding: .9rem 1rem; border: 1px solid #171714; background: #171714; color: #fff; font: inherit; cursor: pointer; }}
    button + button {{ background: #fff; color: #171714; }}
  </style>
</head>
<body>
  <main>
    <h1>Choose a verification identity</h1>
    <p>This provider exists only inside Parserium's isolated verification network.</p>
    <form method="post" action="/authorize">
      {hidden_fields}
      <button type="submit" name="identity" value="user-a">Continue as Workspace A owner</button>
      <button type="submit" name="identity" value="user-b">Continue as Workspace B owner</button>
    </form>
  </main>
</body>
</html>"""
        self._send(
            HTTPStatus.OK,
            page.encode("utf-8"),
            "text/html; charset=utf-8",
        )

    def _complete_authorization(self, parameters: dict[str, str]) -> None:
        identity = IDENTITIES.get(parameters.get("identity", ""))
        if identity is None or not self._valid_authorization_request(parameters):
            self._text(HTTPStatus.BAD_REQUEST, "Invalid authorization request.")
            return
        code = secrets.token_urlsafe(32)
        record = AuthorizationCode(
            client_id=parameters["client_id"],
            redirect_uri=parameters["redirect_uri"],
            state=parameters["state"],
            nonce=parameters["nonce"],
            code_challenge=parameters["code_challenge"],
            identity=identity,
            expires_at=time.monotonic() + 120,
        )
        with self.code_lock:
            self.authorization_codes[code] = record
        self._event(f"authorization code created for {parameters['identity']}")
        location = (
            f"{record.redirect_uri}?{urlencode({'code': code, 'state': record.state})}"
        )
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Pragma", "no-cache")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _exchange_token(self, parameters: dict[str, str]) -> None:
        authentication_kind = (
            "basic"
            if self.headers.get("Authorization", "").startswith("Basic ")
            else "form" if "client_secret" in parameters else "missing"
        )
        self._event(f"token request used {authentication_kind} client authentication")
        if not self._valid_client_authentication():
            self._event("token exchange rejected client authentication")
            self._oauth_error(HTTPStatus.UNAUTHORIZED, "invalid_client")
            return
        code = parameters.get("code", "")
        with self.code_lock:
            record = self.authorization_codes.pop(code, None)
        if record is None or record.expires_at < time.monotonic():
            self._event("token exchange rejected an invalid or expired code")
            self._oauth_error(HTTPStatus.BAD_REQUEST, "invalid_grant")
            return
        verifier = parameters.get("code_verifier", "")
        verifier_digest = hashlib.sha256(
            verifier.encode("ascii", errors="ignore")
        ).digest()
        expected_challenge = (
            base64.urlsafe_b64encode(verifier_digest).rstrip(b"=").decode("ascii")
        )
        valid_exchange = (
            parameters.get("grant_type") == "authorization_code"
            and parameters.get("redirect_uri") == record.redirect_uri
            and hmac.compare_digest(expected_challenge, record.code_challenge)
        )
        if not valid_exchange:
            self._event("token exchange rejected redirect or PKCE validation")
            self._oauth_error(HTTPStatus.BAD_REQUEST, "invalid_grant")
            return

        now = int(time.time())
        claims = {
            "iss": self.configuration.issuer,
            "sub": record.identity.subject,
            "aud": record.client_id,
            "iat": now,
            "exp": now + 300,
            "auth_time": now,
            "nonce": record.nonce,
            "email": record.identity.email,
            "email_verified": True,
            "name": record.identity.name,
        }
        id_token = jwt.encode(
            {"alg": "RS256", "kid": self.signing_key.kid},
            claims,
            self.signing_key,
        )
        self._event(f"ID token issued for {record.identity.subject}")
        self._json(
            HTTPStatus.OK,
            {
                "access_token": secrets.token_urlsafe(24),
                "token_type": "Bearer",
                "expires_in": 300,
                "id_token": id_token,
            },
        )

    def _valid_authorization_request(self, parameters: dict[str, str]) -> bool:
        scopes = set(parameters.get("scope", "").split())
        return (
            parameters.get("client_id") == self.configuration.client_id
            and parameters.get("redirect_uri") == self.configuration.redirect_uri
            and parameters.get("response_type") == "code"
            and parameters.get("code_challenge_method") == "S256"
            and bool(parameters.get("state"))
            and bool(parameters.get("nonce"))
            and bool(parameters.get("code_challenge"))
            and "openid" in scopes
        )

    def _valid_client_authentication(self) -> bool:
        authorization = self.headers.get("Authorization", "")
        if not authorization.startswith("Basic "):
            return False
        try:
            decoded = base64.b64decode(authorization[6:], validate=True).decode("utf-8")
            client_id, client_secret = decoded.split(":", 1)
        except (ValueError, UnicodeDecodeError):
            return False
        return hmac.compare_digest(
            client_id, self.configuration.client_id
        ) and hmac.compare_digest(
            client_secret.encode("utf-8"), self.configuration.client_secret
        )

    def _read_form(self) -> dict[str, str]:
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return {}
        if content_length < 0 or content_length > 65_536:
            return {}
        body = self.rfile.read(content_length).decode("utf-8", errors="strict")
        return self._single_parameters(parse_qs(body))

    @staticmethod
    def _single_parameters(values: dict[str, list[str]]) -> dict[str, str]:
        return {name: items[0] for name, items in values.items() if len(items) == 1}

    def _oauth_error(self, status: HTTPStatus, error: str) -> None:
        self._json(status, {"error": error})

    def _json(self, status: HTTPStatus, value: object) -> None:
        self._send(
            status,
            json.dumps(value, separators=(",", ":")).encode("utf-8"),
            "application/json",
        )

    def _text(self, status: HTTPStatus, value: str) -> None:
        self._send(status, value.encode("utf-8"), "text/plain; charset=utf-8")

    def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Pragma", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    @staticmethod
    def _event(message: str) -> None:
        print(f"OIDC verification: {message}.", flush=True)


def _configuration() -> ProviderConfiguration:
    secret_file = Path(os.environ["OIDC_CLIENT_SECRET_FILE"])
    client_secret = secret_file.read_bytes().strip()
    if len(client_secret) < 32:
        raise ValueError("The verification client secret is invalid.")
    return ProviderConfiguration(
        issuer=os.environ["OIDC_ISSUER"].removesuffix("/"),
        client_id=os.environ["OIDC_CLIENT_ID"],
        client_secret=client_secret,
        redirect_uri=os.environ["OIDC_REDIRECT_URI"],
        certificate_file=Path(os.environ["TLS_CERTIFICATE_FILE"]),
        certificate_key_file=Path(os.environ["TLS_KEY_FILE"]),
        signing_key_file=Path(os.environ["OIDC_SIGNING_KEY_FILE"]),
    )


def main() -> int:
    configuration = _configuration()
    signing_key = RSAKey.import_key(
        configuration.signing_key_file.read_bytes(),
        parameters={"kid": "parserium-verification-key"},
    )
    VerificationOidcHandler.configuration = configuration
    VerificationOidcHandler.signing_key = signing_key
    server = ThreadingHTTPServer(("0.0.0.0", 9443), VerificationOidcHandler)
    tls_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls_context.minimum_version = ssl.TLSVersion.TLSv1_2
    tls_context.load_cert_chain(
        certfile=configuration.certificate_file,
        keyfile=configuration.certificate_key_file,
    )
    server.socket = tls_context.wrap_socket(server.socket, server_side=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
