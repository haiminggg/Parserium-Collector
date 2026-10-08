"""Offline, bounded PDF validation and parsing subprocess."""

import contextlib
import io
import json
import logging
import os
import sys
import time
import tempfile
import zipfile
from pathlib import Path

from pypdf import PdfReader
from parserium_collector.features.analysis.docx import LibreOfficeDocxConverter, DocxConversionError
from parserium_collector.features.analysis.engines import (
    DEFAULT_ENGINE,
    EngineRequest,
    UnknownEngineError,
    get_engine,
    has_extractable_text,
)
from parserium_collector.features.analysis.parser import AnalysisParserTimeoutError


MAX_PAGES = 20
MAX_OUTPUT_BYTES = 10_485_760


class DocumentFailure(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def validate_pdf(source: Path) -> int:
    try:
        with source.open("rb") as stream:
            if stream.read(5) != b"%PDF-":
                raise DocumentFailure("invalid_pdf")
        reader = PdfReader(source, strict=True)
        if reader.is_encrypted:
            raise DocumentFailure("encrypted_pdf")
        page_count = len(reader.pages)
        if page_count < 1:
            raise DocumentFailure("invalid_pdf")
        if page_count > MAX_PAGES:
            raise DocumentFailure("page_limit_exceeded")
        for page in reader.pages:
            _ = page.mediabox
        return page_count
    except DocumentFailure:
        raise
    except Exception as error:
        raise DocumentFailure("invalid_pdf") from error


def parse_pdf(source: Path, engine_id: str = DEFAULT_ENGINE) -> dict[str, object]:
    page_count = validate_pdf(source)
    try:
        engine = get_engine(engine_id)
    except UnknownEngineError:
        return {"ok": False, "error": "invalid_request", "retryable": False}
    request = EngineRequest(
        source=source,
        page_count=page_count,
        page_limit=MAX_PAGES,
        timeout_seconds=45,
        tessdata_path=os.environ.get("PARSERIUM_TESSDATA_PATH") or None,
    )
    started = time.perf_counter()

    def runtime_ms() -> int:
        return min(60_000, max(1, round((time.perf_counter() - started) * 1000)))

    def failure(code: str, *, retryable: bool) -> dict[str, object]:
        return {
            "ok": False,
            "error": code,
            "retryable": retryable,
            "runtimeMs": runtime_ms(),
            "engine": engine_id,
        }

    try:
        # Engine libraries may print progress; stdout must carry only the JSON result.
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            output = engine.parse(request)
    except AnalysisParserTimeoutError:
        return failure("parser_timeout", retryable=True)
    except Exception:
        return failure("parser_failed", retryable=False)
    markdown = output.markdown
    if not has_extractable_text(markdown):
        # A document with no extractable text (for example a scan given to an engine without
        # OCR) must not "succeed" with an empty file.
        return failure("no_text_extracted", retryable=False)
    if len(markdown.encode("utf-8")) > MAX_OUTPUT_BYTES:
        return failure("output_too_large", retryable=False)
    return {
        "ok": True,
        "engine": engine_id,
        "pageCount": output.page_count,
        "tableCount": output.table_count,
        "markdown": markdown,
        "runtimeMs": runtime_ms(),
    }


def result_for(mode: str, source: Path, engine_id: str = DEFAULT_ENGINE) -> dict[str, object]:
    try:
        if source.suffix.lower() == ".docx":
            # Inspect archive bounds before the existing active-content and relationship checks.
            with zipfile.ZipFile(source) as archive:
                members = archive.infolist()
                names = [member.filename for member in members]
                if (len(members) > 2048 or len(set(names)) != len(names)
                    or sum(member.file_size for member in members) > 50_000_000
                    or any(member.file_size > 10_485_760 or member.flag_bits & 1 for member in members)
                    or "word/document.xml" not in names or "[Content_Types].xml" not in names):
                    raise DocumentFailure("invalid_docx")
            with tempfile.TemporaryDirectory(prefix="parserium-convert-") as directory:
                temporary = Path(directory)
                converted = LibreOfficeDocxConverter(temporary_root=temporary, timeout_seconds=15).convert(source)
                if len(converted.pdf_bytes) > MAX_OUTPUT_BYTES:
                    raise DocumentFailure("output_too_large")
                pdf = temporary / "converted.pdf"
                pdf.write_bytes(converted.pdf_bytes)
                return result_for(mode, pdf, engine_id)
        if mode == "validate":
            return {"ok": True, "pageCount": validate_pdf(source)}
        if mode == "parse":
            return parse_pdf(source, engine_id)
        return {"ok": False, "error": "invalid_request", "retryable": False}
    except DocumentFailure as error:
        return {"ok": False, "error": error.code, "retryable": False}
    except (DocxConversionError, zipfile.BadZipFile):
        return {"ok": False, "error": "invalid_docx", "retryable": False}
    except Exception:
        return {"ok": False, "error": "parser_failed", "retryable": False}


def main() -> int:
    logging.disable(logging.CRITICAL)
    if len(sys.argv) not in (3, 4):
        payload = {"ok": False, "error": "invalid_request", "retryable": False}
    else:
        engine_id = sys.argv[3] if len(sys.argv) == 4 else DEFAULT_ENGINE
        payload = result_for(sys.argv[1], Path(sys.argv[2]), engine_id)
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
