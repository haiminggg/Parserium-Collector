import hashlib
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import anyio

from parserium_collector.features.acquisition.downloader import DownloadResult
from parserium_collector.features.acquisition.errors import DownloadTimeoutError
from parserium_collector.features.acquisition.validation import ValidatedDocument
from parserium_collector.features.analysis.docx import ConvertedPdf
from parserium_collector.features.analysis.models import (
    CandidateAnalysisRecord,
    CandidateAnalysisStatus,
    CandidateTableInput,
    TableBoundingBox,
)
from parserium_collector.features.analysis.parser import (
    AnalysisParserError,
    ParsedDocumentAnalysis,
    ParsedTable,
    RenderedPreview,
)
from parserium_collector.features.analysis.worker import AnalysisWorker
from parserium_collector.features.discovery.models import DocumentType
from parserium_collector.features.storage.models import (
    ArtifactKind,
    ArtifactObjectState,
    StoredObjectMetadata,
)
from parserium_collector.features.storage.repository import ArtifactObjectRecord

NOW = datetime(2026, 8, 27, 10, 0, tzinfo=UTC)
PDF_CONTENT = b"%PDF-1.7\ntest-only analysis document"
DOCX_CONTENT = b"PK\x03\x04test-only analysis document"
PREVIEW_CONTENT = b"\x89PNG\r\n\x1a\ntest-only preview"
WORKSPACE_ID = UUID("10000000-0000-4000-8000-000000000001")


def candidate(
    *,
    document_type: DocumentType = DocumentType.PDF,
    attempt_count: int = 1,
) -> CandidateAnalysisRecord:
    return CandidateAnalysisRecord(
        id=uuid4(),
        workspace_id=WORKSPACE_ID,
        session_id=uuid4(),
        ordinal=0,
        source_url=f"https://documents.example/report.{document_type.value}",
        title="Investment report",
        description=None,
        document_type=document_type,
        status=CandidateAnalysisStatus.DOWNLOADING,
        attempt_count=attempt_count,
        available_at=NOW,
        claimed_by="analysis-worker-test",
        lease_expires_at=NOW + timedelta(seconds=60),
        bytes_downloaded=0,
        content_length=None,
        sha256=None,
        media_type=None,
        safe_filename=None,
        page_count=None,
        analyzed_page_count=0,
        table_count=0,
        table_count_lower_bound=False,
        preview_page_num=None,
        preview_width=None,
        preview_height=None,
        error_code=None,
        error_detail=None,
        error_retryable=None,
        promoted_document_id=None,
        created_at=NOW,
        updated_at=NOW,
        started_at=NOW,
        completed_at=None,
        expires_at=NOW + timedelta(hours=1),
    )


def one_table_analysis() -> ParsedDocumentAnalysis:
    cells = (("Fund", "Value"), ("Alpha", "42"))
    return ParsedDocumentAnalysis(
        page_count=3,
        analyzed_page_count=3,
        tables=(
            ParsedTable(
                page_num=2,
                table_index=0,
                bounding_box=TableBoundingBox(
                    x=10,
                    y=20,
                    width=200,
                    height=80,
                ),
                cells=cells,
                markdown="| Fund | Value |\n| --- | --- |\n| Alpha | 42 |",
            ),
        ),
        table_count_lower_bound=False,
        preview_page_num=2,
    )


@dataclass
class AnalysisRepositoryTestDouble:
    claimed: CandidateAnalysisRecord | None
    cleanup_claimed: CandidateAnalysisRecord | None = None
    progress_allowed: bool = True
    events: list[str] = field(default_factory=list)
    progress: list[tuple[int, int | None]] = field(default_factory=list)
    completion: dict[str, Any] | None = None
    failure: dict[str, Any] | None = None
    renewed: anyio.Event = field(default_factory=anyio.Event)
    cleanup_deleted: list[UUID] = field(default_factory=list)

    async def claim_candidate_analysis(
        self,
        worker_id: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> CandidateAnalysisRecord | None:
        record = self.claimed
        self.claimed = None
        return record

    async def claim_expired_candidate_cleanup(
        self,
        worker_id: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> CandidateAnalysisRecord | None:
        record = self.cleanup_claimed
        self.cleanup_claimed = None
        return record

    async def delete_expired_candidate_cleanup(
        self,
        candidate_id: UUID,
        worker_id: str,
        now: datetime,
    ) -> bool:
        self.cleanup_deleted.append(candidate_id)
        return True

    async def renew_candidate_lease(
        self,
        candidate_id: UUID,
        worker_id: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> bool:
        self.events.append("renew")
        self.renewed.set()
        return True

    async def update_candidate_progress(
        self,
        candidate_id: UUID,
        worker_id: str,
        bytes_downloaded: int,
        content_length: int | None,
        now: datetime,
    ) -> bool:
        self.events.append("progress")
        self.progress.append((bytes_downloaded, content_length))
        return self.progress_allowed

    async def transition_candidate_stage(
        self,
        candidate_id: UUID,
        worker_id: str,
        expected_status: CandidateAnalysisStatus,
        next_status: CandidateAnalysisStatus,
        now: datetime,
    ) -> bool:
        self.events.append(next_status.value)
        return True

    async def complete_candidate_analysis(
        self,
        candidate_id: UUID,
        worker_id: str,
        **values: Any,
    ) -> CandidateAnalysisRecord:
        self.events.append("complete")
        self.completion = values
        assert self.claimed is None
        return replace(
            candidate(),
            id=candidate_id,
            status=values["status"],
            completed_at=values["now"],
        )

    async def fail_candidate_analysis(
        self,
        candidate_id: UUID,
        worker_id: str,
        **values: Any,
    ) -> bool:
        self.events.append("fail")
        self.failure = values
        return True


@dataclass
class StagedDownloadTestDouble:
    path: Path
    finished: bool = False

    async def write(self, chunk: bytes) -> None:
        with self.path.open("ab") as handle:
            handle.write(chunk)

    async def finish(self) -> None:
        self.finished = True


@dataclass
class StagingStorageTestDouble:
    temporary_root: Path
    staged: dict[str, StagedDownloadTestDouble] = field(default_factory=dict)
    cleaned: bool = False
    workspace_ids: list[UUID] = field(default_factory=list)

    async def create_file(
        self,
        filename: str,
        *,
        max_bytes: int,
    ) -> StagedDownloadTestDouble:
        assert max_bytes >= len(PDF_CONTENT)
        self.temporary_root.mkdir(parents=True, exist_ok=True)
        path = self.temporary_root / filename
        path.write_bytes(b"")
        staged = StagedDownloadTestDouble(path)
        self.staged[filename] = staged
        return staged

    @asynccontextmanager
    async def job(
        self,
        workspace_id: UUID,
        candidate_id: UUID,
    ) -> AsyncIterator["StagingStorageTestDouble"]:
        self.workspace_ids.append(workspace_id)
        try:
            yield self
        finally:
            for staged in self.staged.values():
                staged.path.unlink(missing_ok=True)
            self.cleaned = True


@dataclass
class DownloaderTestDouble:
    content: bytes
    error: Exception | None = None
    wait_for: anyio.Event | None = None

    async def download(
        self,
        url: str,
        sink: StagedDownloadTestDouble,
        *,
        progress: Callable[[int, int | None], Awaitable[None]] | None = None,
    ) -> DownloadResult:
        if self.error is not None:
            raise self.error
        if self.wait_for is not None:
            await self.wait_for.wait()
        await sink.write(self.content)
        if progress is not None:
            await progress(len(self.content), len(self.content))
        return DownloadResult(
            final_url=url,
            sha256=hashlib.sha256(self.content).hexdigest(),
            size_bytes=len(self.content),
            content_length=len(self.content),
            media_type="application/octet-stream",
        )

    async def aclose(self) -> None:
        return None


class ValidatorTestDouble:
    def __init__(self) -> None:
        self.calls = 0

    def validate(self, path: Path, expected_type: DocumentType) -> ValidatedDocument:
        self.calls += 1
        return ValidatedDocument(
            document_type=expected_type,
            media_type=(
                "application/pdf"
                if expected_type is DocumentType.PDF
                else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            ),
        )


@dataclass
class ArtifactBackendTestDouble:
    writes: list[ArtifactKind] = field(default_factory=list)
    uploads: list[ArtifactObjectRecord] = field(default_factory=list)
    workspace_ids: list[UUID] = field(default_factory=list)
    fail_put_after: int | None = None
    scratch: StagingStorageTestDouble | None = None

    async def begin_upload(
        self,
        workspace_id: UUID,
        storage_key: str,
        object_metadata: StoredObjectMetadata,
        *,
        now: datetime,
    ) -> ArtifactObjectRecord:
        self.workspace_ids.append(workspace_id)
        assert storage_key == object_metadata.storage_key
        record = ArtifactObjectRecord(
            id=uuid4(),
            workspace_id=workspace_id,
            storage_key=storage_key,
            media_type=object_metadata.media_type,
            size_bytes=object_metadata.size_bytes,
            sha256=object_metadata.sha256,
            state=ArtifactObjectState.UPLOADING,
            available_at=None,
            delete_attempt_count=0,
            delete_available_at=None,
            delete_claimed_by=None,
            delete_lease_expires_at=None,
            failure_code=None,
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )
        self.uploads.append(record)
        return record

    async def put_file(
        self,
        storage_key: str,
        source: Path,
        *,
        media_type: str,
        sha256: str,
        size_bytes: int,
    ) -> StoredObjectMetadata:
        if self.fail_put_after is not None and len(self.writes) >= self.fail_put_after:
            raise OSError("test-only object store failure")
        assert await anyio.Path(source).is_file()
        kind = ArtifactKind(storage_key.rsplit("/", 1)[-1])
        self.writes.append(kind)
        return StoredObjectMetadata(
            storage_key=storage_key,
            media_type=media_type,
            size_bytes=size_bytes,
            sha256=sha256,
        )


@dataclass
class ParserTestDouble:
    analysis: ParsedDocumentAnalysis = field(default_factory=one_table_analysis)
    error: Exception | None = None
    wait_for: anyio.Event | None = None
    parsed_paths: list[Path] = field(default_factory=list)
    preview_paths: list[Path] = field(default_factory=list)

    def parse_pdf(self, file_data: bytes | Path) -> ParsedDocumentAnalysis:
        if self.error is not None:
            raise self.error
        if not isinstance(file_data, Path):
            raise AssertionError("The worker must parse a contained PDF path.")
        self.parsed_paths.append(file_data)
        return self.analysis

    def render_preview(self, file_path: Path, *, page_num: int) -> RenderedPreview:
        self.preview_paths.append(file_path)
        return RenderedPreview(
            page_num=page_num,
            width=1275,
            height=1650,
            png_bytes=PREVIEW_CONTENT,
        )


@dataclass
class ConverterTestDouble:
    calls: list[Path] = field(default_factory=list)

    def convert(self, source: Path) -> ConvertedPdf:
        self.calls.append(source)
        return ConvertedPdf(pdf_bytes=PDF_CONTENT)


class ImmediateThenBlock:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, seconds: float) -> None:
        self.calls += 1
        if self.calls == 1:
            return
        await anyio.sleep_forever()


def worker_for(
    tmp_path: Path,
    record: CandidateAnalysisRecord,
    *,
    repository: AnalysisRepositoryTestDouble | None = None,
    downloader: DownloaderTestDouble | None = None,
    parser: ParserTestDouble | None = None,
    artifact_storage: ArtifactBackendTestDouble | None = None,
    lease_waiter: Callable[[float], Awaitable[None]] = anyio.sleep,
) -> tuple[
    AnalysisWorker,
    AnalysisRepositoryTestDouble,
    ValidatorTestDouble,
    ArtifactBackendTestDouble,
    ParserTestDouble,
    ConverterTestDouble,
]:
    resolved_repository = repository or AnalysisRepositoryTestDouble(record)
    resolved_parser = parser or ParserTestDouble()
    validator = ValidatorTestDouble()
    artifacts = artifact_storage or ArtifactBackendTestDouble()
    scratch = StagingStorageTestDouble(tmp_path / "staging")
    artifacts.scratch = scratch
    converter = ConverterTestDouble()
    worker = AnalysisWorker(
        repository=resolved_repository,
        downloader=downloader
        or DownloaderTestDouble(
            PDF_CONTENT if record.document_type is DocumentType.PDF else DOCX_CONTENT
        ),
        validator=validator,
        artifact_repository=artifacts,
        artifact_store=artifacts,
        scratch_storage=scratch,
        parser=resolved_parser,
        converter=converter,
        worker_id="analysis-worker-test",
        lease_seconds=60,
        max_attempts=3,
        retry_base_seconds=30,
        max_artifact_bytes=1024,
        clock=lambda: NOW,
        lease_waiter=lease_waiter,
    )
    return worker, resolved_repository, validator, artifacts, resolved_parser, converter


async def test_pdf_candidate_is_validated_parsed_previewed_and_completed(
    tmp_path: Path,
) -> None:
    record = candidate()
    worker, repository, validator, artifacts, parser, converter = worker_for(
        tmp_path,
        record,
    )

    assert await worker.run_once() is True

    assert repository.events == ["progress", "validating", "parsing", "complete"]
    assert validator.calls == 1
    assert converter.calls == []
    assert artifacts.writes == [
        ArtifactKind.SOURCE_PDF,
        ArtifactKind.PREVIEW_PNG,
    ]
    assert artifacts.workspace_ids == [WORKSPACE_ID, WORKSPACE_ID]
    assert artifacts.scratch is not None
    assert artifacts.scratch.cleaned is True
    assert parser.parsed_paths == parser.preview_paths
    assert repository.completion is not None
    assert repository.completion["status"] is CandidateAnalysisStatus.READY
    assert repository.completion["page_count"] == 3
    assert repository.completion["preview_page_num"] == 2
    assert repository.completion["preview_width"] == 1275
    assert repository.completion["preview_height"] == 1650
    assert repository.completion["safe_filename"] == "Investment report.pdf"
    tables: Sequence[CandidateTableInput] = repository.completion["tables"]
    assert tables[0].cells[1] == ("Alpha", "42")


async def test_docx_candidate_is_converted_before_parsing(tmp_path: Path) -> None:
    record = candidate(document_type=DocumentType.DOCX)
    worker, repository, _, artifacts, parser, converter = worker_for(tmp_path, record)

    await worker.run_once()

    assert repository.events == [
        "progress",
        "validating",
        "converting",
        "parsing",
        "complete",
    ]
    assert len(converter.calls) == 1
    assert artifacts.writes == [
        ArtifactKind.SOURCE_DOCX,
        ArtifactKind.CONVERTED_PDF,
        ArtifactKind.PREVIEW_PNG,
    ]
    assert parser.parsed_paths == parser.preview_paths
    assert repository.completion is not None
    assert [kind for _, kind in repository.completion["artifacts"]] == [
        ArtifactKind.SOURCE_DOCX,
        ArtifactKind.CONVERTED_PDF,
        ArtifactKind.PREVIEW_PNG,
    ]


async def test_page_limited_analysis_uses_safe_partial_state(tmp_path: Path) -> None:
    record = candidate()
    parser = ParserTestDouble(
        analysis=ParsedDocumentAnalysis(
            page_count=250,
            analyzed_page_count=200,
            tables=(),
            table_count_lower_bound=True,
            preview_page_num=1,
        )
    )
    worker, repository, _, _, _, _ = worker_for(tmp_path, record, parser=parser)

    await worker.run_once()

    assert repository.completion is not None
    assert repository.completion["status"] is CandidateAnalysisStatus.PARTIAL
    assert repository.completion["table_count_lower_bound"] is True


async def test_session_budget_rejection_fails_before_validation_and_cleans_scratch(
    tmp_path: Path,
) -> None:
    record = candidate()
    repository = AnalysisRepositoryTestDouble(record, progress_allowed=False)
    worker, repository, validator, artifacts, _, _ = worker_for(
        tmp_path,
        record,
        repository=repository,
    )

    await worker.run_once()

    assert validator.calls == 0
    assert repository.failure is not None
    assert repository.failure["error_code"] == "analysis_budget_exceeded"
    assert repository.failure["retryable"] is False
    assert repository.failure["retry_at"] is None
    assert artifacts.uploads == []
    assert artifacts.scratch is not None
    assert artifacts.scratch.cleaned is True


async def test_retryable_download_failure_uses_bounded_backoff(tmp_path: Path) -> None:
    record = candidate(attempt_count=1)
    worker, repository, _, artifacts, _, _ = worker_for(
        tmp_path,
        record,
        downloader=DownloaderTestDouble(
            PDF_CONTENT,
            error=DownloadTimeoutError("sensitive transport detail"),
        ),
    )

    await worker.run_once()

    assert repository.failure is not None
    assert repository.failure["error_code"] == "download_timeout"
    assert repository.failure["error_detail"] == "The document download timed out."
    assert repository.failure["retryable"] is True
    assert repository.failure["retry_at"] == NOW + timedelta(seconds=30)
    assert artifacts.uploads == []


async def test_parser_failure_is_terminal_and_does_not_expose_raw_error(
    tmp_path: Path,
) -> None:
    record = candidate()
    worker, repository, _, artifacts, _, _ = worker_for(
        tmp_path,
        record,
        parser=ParserTestDouble(
            error=AnalysisParserError("private-path-marker exploded at 192.0.2.2")
        ),
    )

    await worker.run_once()

    assert repository.failure is not None
    assert repository.failure["error_code"] == "analysis_parse_failed"
    assert repository.failure["error_detail"] == "Document table analysis failed."
    assert repository.failure["retryable"] is False
    assert "private-path-marker" not in repository.failure["error_detail"]
    assert [artifact.state for artifact in artifacts.uploads] == [ArtifactObjectState.UPLOADING]


async def test_live_lease_is_renewed_during_analysis(tmp_path: Path) -> None:
    record = candidate()
    repository = AnalysisRepositoryTestDouble(record)
    worker, repository, _, _, _, _ = worker_for(
        tmp_path,
        record,
        repository=repository,
        downloader=DownloaderTestDouble(PDF_CONTENT, wait_for=repository.renewed),
        lease_waiter=ImmediateThenBlock(),
    )

    await worker.run_once()

    assert "renew" in repository.events


async def test_no_available_candidate_returns_false(tmp_path: Path) -> None:
    repository = AnalysisRepositoryTestDouble(None)
    worker, _, _, _, _, _ = worker_for(
        tmp_path,
        candidate(),
        repository=repository,
    )

    assert await worker.run_once() is False


async def test_expired_terminal_candidate_is_deleted_for_registry_cleanup(
    tmp_path: Path,
) -> None:
    expired = replace(
        candidate(),
        status=CandidateAnalysisStatus.READY,
        claimed_by="analysis-worker-test",
        expires_at=NOW,
        completed_at=NOW,
    )
    repository = AnalysisRepositoryTestDouble(None, cleanup_claimed=expired)
    worker, _, _, artifacts, _, _ = worker_for(
        tmp_path,
        expired,
        repository=repository,
    )

    assert await worker.run_once() is True
    assert artifacts.uploads == []
    assert repository.cleanup_deleted == [expired.id]


async def test_partial_upload_failure_leaves_registry_objects_for_maintenance(
    tmp_path: Path,
) -> None:
    record = candidate()
    repository = AnalysisRepositoryTestDouble(record)
    artifacts = ArtifactBackendTestDouble(fail_put_after=1)
    worker, _, _, _, _, _ = worker_for(
        tmp_path,
        record,
        repository=repository,
        artifact_storage=artifacts,
    )

    assert await worker.run_once() is True
    assert repository.failure is not None
    assert repository.failure["error_code"] == "analysis_internal_error"
    assert artifacts.writes == [ArtifactKind.SOURCE_PDF]
    assert [artifact.state for artifact in artifacts.uploads] == [
        ArtifactObjectState.UPLOADING,
        ArtifactObjectState.UPLOADING,
    ]
    assert artifacts.scratch is not None
    assert artifacts.scratch.cleaned is True
