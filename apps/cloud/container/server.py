"""Private HTTP bridge for the offline Parserium PDF subprocess."""

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


MAX_INPUT_BYTES = 10_485_760
MAX_RESPONSE_BYTES = 10_485_760
RUNNER = Path(__file__).with_name("runner.py")
DEFAULT_ENGINE = "liteparse"
# The runner owns the real allowlist; this only rejects malformed values before spawning it.
ENGINE_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")


class Handler(BaseHTTPRequestHandler):
    server_version = "Parserium"
    sys_version = ""

    def log_message(self, *_args: object) -> None:
        return

    def send_json(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(body) > MAX_RESPONSE_BYTES:
            status = 500
            body = b'{"ok":false,"error":"output_too_large","retryable":false}'
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path != "/ping":
            self.send_json(404, {"ok": False, "error": "invalid_request", "retryable": False})
            return
        self.send_response(204)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def do_POST(self) -> None:
        if self.path not in {"/validate", "/parse"}:
            self.send_json(404, {"ok": False, "error": "invalid_request", "retryable": False})
            return
        content_type = self.headers.get_content_type()
        if content_type not in {"application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}:
            self.send_json(415, {"ok": False, "error": "invalid_request", "retryable": False})
            return
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            length = -1
        if length < 1:
            self.send_json(400, {"ok": False, "error": "invalid_request", "retryable": False})
            return
        engine = self.headers.get("X-Parserium-Engine", DEFAULT_ENGINE)
        if not ENGINE_ID.fullmatch(engine):
            self.send_json(400, {"ok": False, "error": "invalid_request", "retryable": False})
            return
        if length > MAX_INPUT_BYTES:
            self.send_json(413, {"ok": False, "error": "upload_too_large", "retryable": False})
            return
        self.connection.settimeout(25)
        body = self.rfile.read(length)
        if len(body) != length:
            self.send_json(400, {"ok": False, "error": "invalid_request", "retryable": False})
            return
        source: Path | None = None
        try:
            suffix = ".pdf" if content_type == "application/pdf" else ".docx"
            with tempfile.NamedTemporaryFile(prefix="parserium-", suffix=suffix, delete=False) as stream:
                source = Path(stream.name)
                stream.write(body)
            completed = subprocess.run(
                [sys.executable, str(RUNNER), self.path.removeprefix("/"), str(source), engine],
                check=False,
                capture_output=True,
                timeout=55 if self.path == "/parse" else 20,
            )
            if completed.returncode != 0 or not completed.stdout or len(completed.stdout) > MAX_RESPONSE_BYTES:
                raise ValueError("runner_failed")
            payload = json.loads(completed.stdout)
            if not isinstance(payload, dict):
                raise ValueError("runner_failed")
            self.send_json(200, payload)
        except subprocess.TimeoutExpired:
            self.send_json(
                200,
                {"ok": False, "error": "parser_timeout", "retryable": True, "runtimeMs": 55_000},
            )
        except Exception:
            self.send_json(503, {"ok": False, "error": "parser_unavailable", "retryable": True})
        finally:
            if source is not None:
                source.unlink(missing_ok=True)


def main() -> None:
    port = int(os.environ.get("PARSERIUM_PORT", "8080"))
    server = HTTPServer(("0.0.0.0", port), Handler)
    server.timeout = 1
    idle_since = time.monotonic()
    # A completed request resets idle time; an active subprocess keeps its own deadline.
    class IdleHandler(Handler):
        def finish(self) -> None:
            nonlocal idle_since
            try:
                super().finish()
            finally:
                idle_since = time.monotonic()
    server.RequestHandlerClass = IdleHandler
    while time.monotonic() - idle_since < 120:
        server.handle_request()
    server.server_close()


if __name__ == "__main__":
    main()
