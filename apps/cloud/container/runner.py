"""Offline, bounded PDF validation and parsing subprocess."""

import json
import logging
import os
import sys
import time
import tempfile
import zipfile
from functools import partial
from pathlib import Path

from liteparse import LiteParse
from pypdf import PdfReader
from parserium_collector.features.analysis.docx import LibreOfficeDocxConverter, DocxConversionError

from parserium_collector.features.analysis.parser import (
    AnalysisParserError,
    AnalysisParserTimeoutError,
    LiteParseAdapter,
)


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


def parse_pdf(source: Path) -> dict[str, object]:
    validate_pdf(source)
    tessdata_path = os.environ.get("PARSERIUM_TESSDATA_PATH")
    parser_factory = (
        partial(LiteParse, tessdata_path=tessdata_path) if tessdata_path else LiteParse
    )
    started = time.perf_counter()
    try:
        analysis = LiteParseAdapter(
            page_limit=MAX_PAGES,
            parser_timeout_seconds=45,
            screenshot_dpi=72,
            parser_factory=parser_factory,
        ).parse_pdf(source)
    except AnalysisParserTimeoutError:
        return {
            "ok": False,
            "error": "parser_timeout",
            "retryable": True,
            "runtimeMs": min(60_000, max(1, round((time.perf_counter() - started) * 1000))),
        }
    except AnalysisParserError:
        return {
            "ok": False,
            "error": "parser_failed",
            "retryable": False,
            "runtimeMs": min(60_000, max(1, round((time.perf_counter() - started) * 1000))),
        }
    markdown = analysis.markdown
    if len(markdown.encode("utf-8")) > MAX_OUTPUT_BYTES:
        return {
            "ok": False,
            "error": "output_too_large",
            "retryable": False,
            "runtimeMs": min(60_000, max(1, round((time.perf_counter() - started) * 1000))),
        }
    return {
        "ok": True,
        "pageCount": analysis.page_count,
        "tableCount": len(analysis.tables),
        "markdown": markdown,
        "runtimeMs": min(60_000, max(1, round((time.perf_counter() - started) * 1000))),
    }


def result_for(mode: str, source: Path) -> dict[str, object]:
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
                return result_for(mode, pdf)
        if mode == "validate":
            return {"ok": True, "pageCount": validate_pdf(source)}
        if mode == "parse":
            return parse_pdf(source)
        return {"ok": False, "error": "invalid_request", "retryable": False}
    except DocumentFailure as error:
        return {"ok": False, "error": error.code, "retryable": False}
    except (DocxConversionError, zipfile.BadZipFile):
        return {"ok": False, "error": "invalid_docx", "retryable": False}
    except Exception:
        return {"ok": False, "error": "parser_failed", "retryable": False}


def main() -> int:
    logging.disable(logging.CRITICAL)
    if len(sys.argv) != 3:
        payload = {"ok": False, "error": "invalid_request", "retryable": False}
    else:
        payload = result_for(sys.argv[1], Path(sys.argv[2]))
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
