import hashlib
from collections.abc import Awaitable, Callable, Sequence
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Protocol
from urllib.parse import unquote, urlsplit
from uuid import UUID

import anyio

from parserium_collector.features.acquisition.downloader import (
    AsyncByteSink,
    DownloadResult,
    ProgressCallback,
)
from parserium_collector.features.acquisition.errors import AcquisitionError
from parserium_collector.features.acquisition.export_storage import safe_windows_filename
from parserium_collector.features.acquisition.validation import ValidatedDocument
from parserium_collector.features.analysis.docx import (
    ConvertedPdf,
    DocxConversionError,
    DocxConversionTimeoutError,
)
from parserium_collector.features.analysis.models import (
    CandidateAnalysisRecord,
    CandidateAnalysisStatus,
    CandidateTableInput,
)
from parserium_collector.features.analysis.parser import (
    AnalysisParserError,
    AnalysisParserTimeoutError,
    ParsedDocumentAnalysis,
    RenderedPreview,
)
from parserium_collector.features.analysis.repository import AnalysisRepository
from parserium_collector.features.discovery.models import DocumentType
from parserium_collector.features.storage.keys import artifact_key
from parserium_collector.features.storage.models import (
    ArtifactKind,
    ArtifactResourceKind,
    StoredObjectMetadata,
)
from parserium_collector.features.storage.protocols import ArtifactStore
from parserium_collector.features.storage.repository import ArtifactObjectRecord


class StagedAnalysisDownload(AsyncByteSink, Protocol):
    path: Path

    async def finish(self) -> None: ...


class AnalysisScratchJob(Protocol):
    async def create_file(
        self,
        filename: str,
        *,
        max_bytes: int,
    ) -> StagedAnalysisDownload: ...


class AnalysisScratchStorage(Protocol):
    def job(
        self,
        workspace_id: UUID,
        job_id: UUID,
    ) -> AbstractAsyncContextManager[AnalysisScratchJob]: ...


class AnalysisDownloader(Protocol):
    async def download(
        self,
        url: str,
        sink: AsyncByteSink,
        *,
        progress: ProgressCallback | None = None,
    ) -> DownloadResult: ...

    async def aclose(self) -> None: ...


class AnalysisValidator(Protocol):
    def validate(self, path: Path, expected_type: DocumentType) -> ValidatedDocument: ...


class AnalysisParser(Protocol):
    def parse_pdf(self, file_data: bytes | Path) -> ParsedDocumentAnalysis: ...

    def render_preview(self, file_path: Path, *, page_num: int) -> RenderedPreview: ...


class AnalysisDocxConverter(Protocol):
    def convert(self, source: Path) -> ConvertedPdf: ...


class AnalysisArtifactRepository(Protocol):
    async def begin_upload(
        self,
        workspace_id: UUID,
        storage_key: str,
        object_metadata: StoredObjectMetadata,
        *,
        now: datetime,
    ) -> ArtifactObjectRecord: ...


class AnalysisLeaseLostError(RuntimeError):
    pass


class AnalysisBudgetExceededError(RuntimeError):
    pass


class AnalysisWorker:
    def __init__(
        self,
        *,
        repository: AnalysisRepository,
        artifact_repository: AnalysisArtifactRepository,
        artifact_store: ArtifactStore,
        scratch_storage: AnalysisScratchStorage,
        downloader: AnalysisDownloader,
        validator: AnalysisValidator,
        parser: AnalysisParser,
        converter: AnalysisDocxConverter,
        worker_id: str,
        lease_seconds: int,
        max_attempts: int,
        retry_base_seconds: int,
        max_artifact_bytes: int,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        lease_waiter: Callable[[float], Awaitable[None]] = anyio.sleep,
    ) -> None:
        if (
            lease_seconds < 1
            or max_attempts < 1
            or retry_base_seconds < 1
            or max_artifact_bytes < 1
        ):
            raise ValueError("Worker lease and retry settings must be positive.")
        self._repository = repository
        self._artifact_repository = artifact_repository
        self._artifact_store = artifact_store
        self._scratch_storage = scratch_storage
        self._downloader = downloader
        self._validator = validator
        self._parser = parser
        self._converter = converter
        self._worker_id = worker_id
        self._lease_seconds = lease_seconds
        self._lease_interval = lease_seconds / 3
        self._max_attempts = max_attempts
        self._retry_base_seconds = retry_base_seconds
        self._max_artifact_bytes = max_artifact_bytes
        self._clock = clock
        self._lease_waiter = lease_waiter

    async def run_once(self) -> bool:
        now = self._clock()
        candidate = await self._repository.claim_candidate_analysis(
            self._worker_id,
            now,
            now + timedelta(seconds=self._lease_seconds),
        )
        if candidate is None:
            cleanup_candidate = await self._repository.claim_expired_candidate_cleanup(
                self._worker_id,
                now,
                now + timedelta(seconds=self._lease_seconds),
            )
            if cleanup_candidate is None:
                return False
            await self._cleanup_expired_candidate(cleanup_candidate)
            return True
        await self._handle_candidate(candidate)
        return True

    async def aclose(self) -> None:
        await self._downloader.aclose()

    async def _cleanup_expired_candidate(
        self,
        candidate: CandidateAnalysisRecord,
    ) -> None:
        await self._repository.delete_expired_candidate_cleanup(
            candidate.id,
            self._worker_id,
            self._clock(),
        )

    async def _handle_candidate(self, candidate: CandidateAnalysisRecord) -> None:
        async def operation() -> None:
            await self._process_candidate(candidate)

        async def renew(now: datetime, lease_expires_at: datetime) -> bool:
            return await self._repository.renew_candidate_lease(
                candidate.id,
                self._worker_id,
                now,
                lease_expires_at,
            )

        try:
            await self._run_with_lease(operation, renew)
        except AnalysisLeaseLostError:
            return
        except BaseException as error:
            if isinstance(error, anyio.get_cancelled_exc_class()):
                raise
            code, detail, retryable = self._classify_error(error)
            now = self._clock()
            await self._repository.fail_candidate_analysis(
                candidate.id,
                self._worker_id,
                error_code=code,
                error_detail=detail,
                retryable=retryable,
                retry_at=self._retry_at(candidate.attempt_count, retryable, now),
                now=now,
            )

    async def _process_candidate(self, candidate: CandidateAnalysisRecord) -> None:
        async with self._scratch_storage.job(
            candidate.workspace_id,
            candidate.id,
        ) as scratch:
            staged = await scratch.create_file(
                f"source.{candidate.document_type.value}",
                max_bytes=self._max_artifact_bytes,
            )

            async def update_progress(downloaded: int, total: int | None) -> None:
                accepted = await self._repository.update_candidate_progress(
                    candidate.id,
                    self._worker_id,
                    downloaded,
                    total,
                    self._clock(),
                )
                if not accepted:
                    raise AnalysisBudgetExceededError(
                        "The analysis session byte limit was reached."
                    )

            result = await self._downloader.download(
                candidate.source_url,
                staged,
                progress=update_progress,
            )
            await staged.finish()
            await self._transition(
                candidate.id,
                CandidateAnalysisStatus.DOWNLOADING,
                CandidateAnalysisStatus.VALIDATING,
            )
            validated = await anyio.to_thread.run_sync(
                self._validator.validate,
                staged.path,
                candidate.document_type,
            )
            source_kind = (
                ArtifactKind.SOURCE_PDF
                if validated.document_type is DocumentType.PDF
                else ArtifactKind.SOURCE_DOCX
            )
            source = await self._upload_artifact(
                candidate,
                source_kind,
                staged.path,
                media_type=validated.media_type,
                sha256=result.sha256,
                size_bytes=result.size_bytes,
            )
            artifacts = [(source.id, source_kind)]
            if validated.document_type is DocumentType.DOCX:
                await self._transition(
                    candidate.id,
                    CandidateAnalysisStatus.VALIDATING,
                    CandidateAnalysisStatus.CONVERTING,
                )
                converted_pdf = await anyio.to_thread.run_sync(
                    self._converter.convert,
                    staged.path,
                )
                converted_file = await scratch.create_file(
                    "converted.pdf",
                    max_bytes=self._max_artifact_bytes,
                )
                await converted_file.write(converted_pdf.pdf_bytes)
                await converted_file.finish()
                converted = await self._upload_artifact(
                    candidate,
                    ArtifactKind.CONVERTED_PDF,
                    converted_file.path,
                    media_type="application/pdf",
                    sha256=hashlib.sha256(converted_pdf.pdf_bytes).hexdigest(),
                    size_bytes=len(converted_pdf.pdf_bytes),
                )
                artifacts.append((converted.id, ArtifactKind.CONVERTED_PDF))
                await self._transition(
                    candidate.id,
                    CandidateAnalysisStatus.CONVERTING,
                    CandidateAnalysisStatus.PARSING,
                )
                pdf_path = converted_file.path
            else:
                await self._transition(
                    candidate.id,
                    CandidateAnalysisStatus.VALIDATING,
                    CandidateAnalysisStatus.PARSING,
                )
                pdf_path = staged.path

            analysis = await anyio.to_thread.run_sync(
                self._parser.parse_pdf,
                pdf_path,
            )
            preview = await anyio.to_thread.run_sync(
                self._render_preview,
                pdf_path,
                analysis.preview_page_num,
            )
            preview_file = await scratch.create_file(
                "preview.png",
                max_bytes=self._max_artifact_bytes,
            )
            await preview_file.write(preview.png_bytes)
            await preview_file.finish()
            preview_object = await self._upload_artifact(
                candidate,
                ArtifactKind.PREVIEW_PNG,
                preview_file.path,
                media_type="image/png",
                sha256=hashlib.sha256(preview.png_bytes).hexdigest(),
                size_bytes=len(preview.png_bytes),
            )
            artifacts.append((preview_object.id, ArtifactKind.PREVIEW_PNG))
            await self._repository.complete_candidate_analysis(
                candidate.id,
                self._worker_id,
                status=self._terminal_status(analysis),
                sha256=result.sha256,
                media_type=validated.media_type,
                safe_filename=self._safe_filename(candidate),
                artifacts=artifacts,
                page_count=analysis.page_count,
                analyzed_page_count=analysis.analyzed_page_count,
                table_count_lower_bound=analysis.table_count_lower_bound,
                preview_page_num=preview.page_num,
                preview_width=preview.width,
                preview_height=preview.height,
                tables=self._table_inputs(analysis),
                now=self._clock(),
            )

    async def _upload_artifact(
        self,
        candidate: CandidateAnalysisRecord,
        kind: ArtifactKind,
        path: Path,
        *,
        media_type: str,
        sha256: str,
        size_bytes: int,
    ) -> ArtifactObjectRecord:
        storage_key = artifact_key(
            candidate.workspace_id,
            ArtifactResourceKind.ANALYSIS,
            candidate.id,
            kind,
        )
        expected = StoredObjectMetadata(
            storage_key=storage_key,
            media_type=media_type,
            size_bytes=size_bytes,
            sha256=sha256,
        )
        artifact_object = await self._artifact_repository.begin_upload(
            candidate.workspace_id,
            storage_key,
            expected,
            now=self._clock(),
        )
        actual = await self._artifact_store.put_file(
            storage_key,
            path,
            media_type=media_type,
            sha256=sha256,
            size_bytes=size_bytes,
        )
        if actual != expected:
            raise RuntimeError("Stored analysis artifact metadata did not match the upload.")
        return artifact_object

    def _render_preview(self, pdf_path: Path, page_num: int) -> RenderedPreview:
        return self._parser.render_preview(pdf_path, page_num=page_num)

    async def _transition(
        self,
        candidate_id: UUID,
        expected: CandidateAnalysisStatus,
        next_status: CandidateAnalysisStatus,
    ) -> None:
        transitioned = await self._repository.transition_candidate_stage(
            candidate_id,
            self._worker_id,
            expected,
            next_status,
            self._clock(),
        )
        if not transitioned:
            raise AnalysisLeaseLostError("The analysis candidate lease was lost.")

    async def _run_with_lease(
        self,
        operation: Callable[[], Awaitable[None]],
        renew: Callable[[datetime, datetime], Awaitable[bool]],
    ) -> None:
        completed = anyio.Event()
        errors: list[BaseException] = []

        async def run_operation() -> None:
            try:
                await operation()
            except BaseException as error:
                errors.append(error)
            finally:
                completed.set()

        async def renew_lease() -> None:
            while True:
                await self._lease_waiter(self._lease_interval)
                if completed.is_set():
                    return
                now = self._clock()
                try:
                    renewed = await renew(
                        now,
                        now + timedelta(seconds=self._lease_seconds),
                    )
                except BaseException as error:
                    errors.append(error)
                    completed.set()
                    return
                if not renewed:
                    errors.append(AnalysisLeaseLostError("The analysis candidate lease was lost."))
                    completed.set()
                    return

        async with anyio.create_task_group() as task_group:
            task_group.start_soon(run_operation)
            task_group.start_soon(renew_lease)
            await completed.wait()
            task_group.cancel_scope.cancel()
        if errors:
            raise errors[0]

    def _retry_at(
        self,
        attempt_count: int,
        retryable: bool,
        now: datetime,
    ) -> datetime | None:
        if not retryable or attempt_count >= self._max_attempts:
            return None
        delay = min(
            self._retry_base_seconds * (2 ** max(attempt_count - 1, 0)),
            3600,
        )
        return now + timedelta(seconds=delay)

    @staticmethod
    def _terminal_status(analysis: ParsedDocumentAnalysis) -> CandidateAnalysisStatus:
        if analysis.table_count_lower_bound:
            return CandidateAnalysisStatus.PARTIAL
        if analysis.tables:
            return CandidateAnalysisStatus.READY
        return CandidateAnalysisStatus.NO_TABLES

    @staticmethod
    def _table_inputs(
        analysis: ParsedDocumentAnalysis,
    ) -> Sequence[CandidateTableInput]:
        return tuple(
            CandidateTableInput(
                page_num=table.page_num,
                table_index=table.table_index,
                bounding_box=table.bounding_box,
                cells=table.cells,
                markdown=table.markdown,
            )
            for table in analysis.tables
        )

    @staticmethod
    def _safe_filename(candidate: CandidateAnalysisRecord) -> str:
        path_name = PurePosixPath(unquote(urlsplit(candidate.source_url).path)).name
        return safe_windows_filename(
            candidate.title or path_name or "document",
            candidate.document_type,
        )

    @staticmethod
    def _classify_error(error: BaseException) -> tuple[str, str, bool]:
        if isinstance(error, AnalysisBudgetExceededError):
            return (
                "analysis_budget_exceeded",
                "The analysis session byte limit was reached.",
                False,
            )
        if isinstance(error, AnalysisParserTimeoutError):
            return (
                "analysis_parse_timeout",
                "Document table analysis timed out.",
                True,
            )
        if isinstance(error, AnalysisParserError):
            return (
                "analysis_parse_failed",
                "Document table analysis failed.",
                False,
            )
        if isinstance(error, DocxConversionTimeoutError):
            return (
                "analysis_conversion_timeout",
                "DOCX conversion timed out.",
                True,
            )
        if isinstance(error, DocxConversionError):
            return (
                "analysis_conversion_failed",
                "DOCX conversion failed.",
                False,
            )
        if isinstance(error, AcquisitionError):
            if error.code == "download_timeout":
                detail = "The document download timed out."
            elif error.code == "blocked_destination":
                detail = "The document address is not permitted."
            elif error.code == "invalid_document":
                detail = "The downloaded document is invalid."
            else:
                detail = "Document acquisition failed."
            return error.code, detail, error.retryable
        return (
            "analysis_internal_error",
            "The analysis worker encountered an internal error.",
            False,
        )
