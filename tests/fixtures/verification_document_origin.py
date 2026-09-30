"""Test-only HTTP origin for deterministic acquisition verification."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PDF = (Path(__file__).resolve().parent / "analysis" / "ruled-table.pdf").read_bytes()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/health":
            body = b"ok"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
        elif self.path == "/investment-table.pdf":
            body = PDF
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
        else:
            body = b"not found"
            self.send_response(404)
            self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        del format, args


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8090), Handler).serve_forever()
