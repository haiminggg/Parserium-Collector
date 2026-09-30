"""Test-only Firecrawl-compatible metadata service for browser verification."""

import argparse
import hmac
import json
import os
import ssl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

BEARER_PATH = Path(
    os.environ.get(
        "TEST_FIRECRAWL_BEARER_FILE",
        "/run/secrets/firecrawl_test_bearer",
    )
)
CERTIFICATE_PATH = os.environ.get(
    "TLS_CERTIFICATE_FILE",
    "/run/secrets/tls_certificate",
)
PRIVATE_KEY_PATH = os.environ.get(
    "TLS_KEY_FILE",
    "/run/secrets/tls_private_key",
)


class Handler(BaseHTTPRequestHandler):
    expected_bearer = ""
    require_authorization = True
    authorization_matched = False

    def do_GET(self) -> None:
        if self.path in {"/health", "/v0/health/liveness"}:
            self._json(200, {"status": "ok"})
            return
        if self.path == "/test-state":
            self._json(
                200,
                {"authorization_matched": type(self).authorization_matched},
            )
            return
        self._json(404, {"success": False})

    def do_POST(self) -> None:
        if self.path != "/v2/search":
            self._json(404, {"success": False})
            return
        if self.require_authorization:
            supplied = self.headers.get("Authorization", "")
            expected = f"Bearer {self.expected_bearer}"
            if not hmac.compare_digest(supplied, expected):
                self._json(401, {"success": False, "error": "Unauthorized"})
                return
            type(self).authorization_matched = True
        try:
            length = int(self.headers.get("Content-Length", "0"))
            request = json.loads(self.rfile.read(length))
        except (ValueError, json.JSONDecodeError):
            self._json(400, {"success": False})
            return
        if request.get("sources") != [{"type": "web"}] or request.get("highlights") is not False:
            self._json(400, {"success": False})
            return
        self._json(
            200,
            {
                "success": True,
                "id": "verification-search",
                "data": {
                    "web": [
                        {
                            "url": "http://test-origin:8090/investment-table.pdf",
                            "title": "Deterministic investment table",
                            "description": "Test-only valid PDF candidate",
                        },
                        {
                            "url": "http://test-origin:8092/blocked-investment-table.pdf",
                            "title": "Blocked investment table",
                            "description": "Test-only prohibited-port candidate",
                        },
                    ]
                },
                "creditsUsed": 0,
            },
        )

    def _json(self, status: int, value: dict[str, Any]) -> None:
        body = json.dumps(value, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        del format, args


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--legacy-http", action="store_true")
    args = parser.parse_args()

    if args.legacy_http:
        Handler.require_authorization = False
        server = ThreadingHTTPServer(("0.0.0.0", 8091), Handler)  # noqa: S104
    else:
        Handler.expected_bearer = BEARER_PATH.read_text(encoding="utf-8").strip()
        if not Handler.expected_bearer:
            raise RuntimeError("The generated Firecrawl test bearer must not be empty.")
        server = ThreadingHTTPServer(("0.0.0.0", 9444), Handler)  # noqa: S104
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(CERTIFICATE_PATH, PRIVATE_KEY_PATH)
        server.socket = context.wrap_socket(server.socket, server_side=True)

    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
