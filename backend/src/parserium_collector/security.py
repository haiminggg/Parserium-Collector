from collections.abc import Iterable
from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class LocalRequestGuardMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        allowed_hosts: Iterable[str],
        allowed_origins: Iterable[str],
    ) -> None:
        self._app = app
        self._allowed_hosts = frozenset(host.lower() for host in allowed_hosts)
        self._allowed_origins = frozenset(allowed_origins)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        host = self._hostname(headers.get("host"))
        if host is None or host.lower() not in self._allowed_hosts:
            await JSONResponse({"detail": "Invalid host."}, status_code=400)(scope, receive, send)
            return

        method = str(scope.get("method", "GET")).upper()
        path = str(scope.get("path", ""))
        if method in UNSAFE_METHODS and path.startswith("/api/"):
            if headers.get("origin") not in self._allowed_origins:
                await JSONResponse({"detail": "Invalid origin."}, status_code=403)(
                    scope, receive, send
                )
                return

        await self._app(scope, receive, send)

    @staticmethod
    def _hostname(host_header: str | None) -> str | None:
        if host_header is None:
            return None
        try:
            return urlsplit(f"//{host_header}").hostname
        except ValueError:
            return None
