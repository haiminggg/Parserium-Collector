import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SERVER = ROOT / "apps" / "cloud" / "container" / "server.py"
RULED_TABLE = ROOT / "tests" / "fixtures" / "analysis" / "ruled-table.pdf"


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


@contextmanager
def running_server() -> Iterator[str]:
    port = free_port()
    env = {**os.environ, "PARSERIUM_PORT": str(port)}
    process = subprocess.Popen(
        [sys.executable, str(SERVER)],
        cwd=SERVER.parent,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            if process.poll() is not None:
                stdout, stderr = process.communicate()
                raise AssertionError(f"server exited early: {stdout!r} {stderr!r}")
            try:
                with urllib.request.urlopen(f"{base}/ping", timeout=0.1) as response:
                    if response.status == 204:
                        break
            except (OSError, urllib.error.URLError):
                time.sleep(0.02)
        else:
            raise AssertionError("server did not become ready")
        yield base
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def request(base: str, path: str, body: bytes, content_type: str = "application/pdf"):
    return urllib.request.urlopen(
        urllib.request.Request(
            f"{base}{path}",
            data=body,
            method="POST",
            headers={"Content-Type": content_type},
        ),
        timeout=20,
    )


def test_server_validates_real_pdf_without_exposing_a_file_path() -> None:
    with running_server() as base:
        with request(base, "/validate", RULED_TABLE.read_bytes()) as response:
            result = json.loads(response.read())

    assert response.status == 200
    assert result == {"ok": True, "pageCount": 1}


def test_server_rejects_wrong_media_type_and_unknown_path() -> None:
    with running_server() as base:
        for path, media_type, expected in (
            ("/validate", "text/plain", 415),
            ("/other", "application/pdf", 404),
        ):
            try:
                request(base, path, b"%PDF-invalid", media_type)
            except urllib.error.HTTPError as error:
                assert error.code == expected
                assert json.loads(error.read()) == {
                    "ok": False,
                    "error": "invalid_request",
                    "retryable": False,
                }
            else:
                raise AssertionError("invalid request was accepted")


def test_server_rejects_declared_oversize_before_reading_body() -> None:
    with running_server() as base:
        raw = socket.create_connection(("127.0.0.1", int(base.rsplit(":", 1)[1])), timeout=5)
        with raw:
            raw.sendall(
                b"POST /parse HTTP/1.1\r\n"
                b"Host: 127.0.0.1\r\n"
                b"Content-Type: application/pdf\r\n"
                b"Content-Length: 10485761\r\n\r\n"
            )
            chunks: list[bytes] = []
            while chunk := raw.recv(4096):
                chunks.append(chunk)
            response = b"".join(chunks)

    assert b" 413 " in response
    assert b"upload_too_large" in response
