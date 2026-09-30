from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import anyio
import pytest
from anyio.abc import SocketAttribute, SocketStream
from pypdf import PdfWriter

from parserium_collector.features.acquisition.downloader import BoundedDownloader
from parserium_collector.features.acquisition.errors import BlockedDestinationError
from parserium_collector.features.acquisition.models import DocumentType
from parserium_collector.features.acquisition.network_policy import NetworkPolicy
from parserium_collector.features.acquisition.validation import DocumentValidator
from parserium_collector.features.storage.filesystem import FilesystemArtifactStore
from parserium_collector.features.storage.keys import artifact_key
from parserium_collector.features.storage.models import ArtifactKind, ArtifactResourceKind
from parserium_collector.features.storage.scratch import ScratchStorage

WORKSPACE_A = UUID("10000000-0000-4000-8000-000000000001")
WORKSPACE_B = UUID("10000000-0000-4000-8000-000000000002")


def make_test_pdf_bytes() -> bytes:
    output = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.write(output)
    return output.getvalue()


@dataclass
class LocalOrigin:
    base_url: str
    pdf_bytes: bytes
    request_paths: list[str] = field(default_factory=list)


@asynccontextmanager
async def serve_local_origin() -> AsyncIterator[LocalOrigin]:
    listener = await anyio.create_tcp_listener(local_host="127.0.0.1", local_port=0)
    _, port = listener.extra(SocketAttribute.local_address)  # noqa: S610
    origin = LocalOrigin(
        base_url=f"http://127.0.0.1:{port}",
        pdf_bytes=make_test_pdf_bytes(),
    )

    async def handle(stream: SocketStream) -> None:
        try:
            request = bytearray()
            while b"\r\n\r\n" not in request:
                request.extend(await stream.receive())
            request_line = bytes(request).split(b"\r\n", maxsplit=1)[0]
            _, raw_target, _ = request_line.split(b" ", maxsplit=2)
            path = urlsplit(raw_target.decode("ascii")).path
            origin.request_paths.append(path)
            if path == "/redirect.pdf":
                response = (
                    b"HTTP/1.1 302 Found\r\n"
                    b"Location: /document.pdf\r\n"
                    b"Content-Length: 0\r\n"
                    b"Connection: close\r\n\r\n"
                )
            elif path == "/private-redirect.pdf":
                response = (
                    b"HTTP/1.1 302 Found\r\n"
                    + f"Location: http://localhost:{port}/document.pdf\r\n".encode()
                    + b"Content-Length: 0\r\n"
                    + b"Connection: close\r\n\r\n"
                )
            else:
                response = (
                    b"HTTP/1.1 200 OK\r\n"
                    b"Content-Type: application/pdf\r\n"
                    + f"Content-Length: {len(origin.pdf_bytes)}\r\n".encode()
                    + b"Connection: close\r\n\r\n"
                    + origin.pdf_bytes
                )
            await stream.send(response)
        finally:
            await stream.aclose()

    async with listener, anyio.create_task_group() as task_group:
        task_group.start_soon(listener.serve, handle)
        yield origin
        task_group.cancel_scope.cancel()


def make_storage(tmp_path: Path) -> tuple[ScratchStorage, FilesystemArtifactStore]:
    scratch_root = tmp_path / "scratch"
    artifact_root = tmp_path / "artifacts"
    scratch_root.mkdir()
    artifact_root.mkdir()
    scratch = ScratchStorage(
        scratch_root,
        reserve_bytes=0,
        reserve_ratio=0,
    )
    artifacts = FilesystemArtifactStore(
        artifact_root,
        reserve_bytes=0,
        reserve_ratio=0,
    )
    return scratch, artifacts


def make_downloader(origin: LocalOrigin, *, allow_private: bool) -> BoundedDownloader:
    port = urlsplit(origin.base_url).port
    assert port is not None
    return BoundedDownloader(
        policy=NetworkPolicy(
            allowed_public_ports=(80, 443),
            private_allowlist=(f"127.0.0.1:{port}",) if allow_private else (),
        ),
        max_bytes=1024 * 1024,
        connect_timeout=2,
        read_timeout=2,
        total_timeout=10,
        max_redirects=5,
    )


async def test_real_local_pipeline_enforces_policy_redirects_validation_and_artifact_upload(
    tmp_path: Path,
) -> None:
    async with serve_local_origin() as local_origin:
        blocked_downloader = make_downloader(local_origin, allow_private=False)
        scratch, artifacts = make_storage(tmp_path)
        try:
            async with scratch.job(WORKSPACE_A, uuid4()) as job:
                staged = await job.create_file("source.pdf", max_bytes=1024 * 1024)
                with pytest.raises(BlockedDestinationError):
                    await blocked_downloader.download(
                        f"{local_origin.base_url}/document.pdf",
                        staged,
                    )
        finally:
            await blocked_downloader.aclose()
        assert local_origin.request_paths == []

        downloader = make_downloader(local_origin, allow_private=True)
        validator = DocumentValidator(
            docx_max_expanded_bytes=10 * 1024 * 1024,
            docx_max_expansion_ratio=100,
        )
        try:
            first_document_id = uuid4()
            async with scratch.job(WORKSPACE_A, uuid4()) as job:
                staged = await job.create_file("source.pdf", max_bytes=1024 * 1024)
                first_download = await downloader.download(
                    f"{local_origin.base_url}/redirect.pdf",
                    staged,
                )
                await staged.finish()
                validated = await anyio.to_thread.run_sync(
                    validator.validate,
                    staged.path,
                    DocumentType.PDF,
                )
                first_key = artifact_key(
                    WORKSPACE_A,
                    ArtifactResourceKind.DOCUMENT,
                    first_document_id,
                    ArtifactKind.STORED_DOCUMENT,
                )
                first = await artifacts.put_file(
                    first_key,
                    staged.path,
                    media_type=validated.media_type,
                    sha256=first_download.sha256,
                    size_bytes=first_download.size_bytes,
                )

            second_document_id = uuid4()
            async with scratch.job(WORKSPACE_A, uuid4()) as job:
                staged = await job.create_file("source.pdf", max_bytes=1024 * 1024)
                second_download = await downloader.download(
                    f"{local_origin.base_url}/same.pdf",
                    staged,
                )
                await staged.finish()
                second_key = artifact_key(
                    WORKSPACE_A,
                    ArtifactResourceKind.DOCUMENT,
                    second_document_id,
                    ArtifactKind.STORED_DOCUMENT,
                )
                second = await artifacts.put_file(
                    second_key,
                    staged.path,
                    media_type="application/pdf",
                    sha256=second_download.sha256,
                    size_bytes=second_download.size_bytes,
                )

            third_document_id = uuid4()
            async with scratch.job(WORKSPACE_B, uuid4()) as job:
                staged = await job.create_file("source.pdf", max_bytes=1024 * 1024)
                third_download = await downloader.download(
                    f"{local_origin.base_url}/workspace-b.pdf",
                    staged,
                )
                await staged.finish()
                third_key = artifact_key(
                    WORKSPACE_B,
                    ArtifactResourceKind.DOCUMENT,
                    third_document_id,
                    ArtifactKind.STORED_DOCUMENT,
                )
                third = await artifacts.put_file(
                    third_key,
                    staged.path,
                    media_type="application/pdf",
                    sha256=third_download.sha256,
                    size_bytes=third_download.size_bytes,
                )

            async with scratch.job(WORKSPACE_A, uuid4()) as job:
                staged = await job.create_file("source.pdf", max_bytes=1024 * 1024)
                with pytest.raises(BlockedDestinationError):
                    await downloader.download(
                        f"{local_origin.base_url}/private-redirect.pdf",
                        staged,
                    )
        finally:
            await downloader.aclose()

        assert first.sha256 == second.sha256 == third.sha256
        assert first.storage_key != second.storage_key != third.storage_key
        assert third.storage_key.startswith(f"workspaces/{WORKSPACE_B}/document/")
        async with artifacts.open_reader(first.storage_key) as reader:
            assert await reader.read() == local_origin.pdf_bytes
        assert not any((tmp_path / "scratch" / "workspaces").rglob("source.pdf"))
        assert local_origin.request_paths == [
            "/redirect.pdf",
            "/document.pdf",
            "/same.pdf",
            "/workspace-b.pdf",
            "/private-redirect.pdf",
        ]
