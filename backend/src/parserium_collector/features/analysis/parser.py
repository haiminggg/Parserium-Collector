import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from typing import Literal, Protocol, cast

from liteparse import (
    LiteParse,
    ParseResult,
    ParseTimeoutError,
    ScreenshotResult,
)
from liteparse.types import LayoutBlock, ParsedPage

from parserium_collector.features.analysis.models import TableBoundingBox

OcrMode = Literal["auto", "always", "never"]

# LiteParse 2.14.0 joins per-page Markdown with this separator. A real-parser test pins it.
LITEPARSE_PAGE_SEPARATOR = "\n\n-----\n\n"


class AnalysisParserError(RuntimeError):
    """A safe document-analysis failure suitable for worker classification."""


class AnalysisParserTimeoutError(AnalysisParserError):
    """The bounded parser operation exceeded its configured wall-clock limit."""


@dataclass(frozen=True)
class ParsedTable:
    page_num: int
    table_index: int
    bounding_box: TableBoundingBox
    cells: tuple[tuple[str, ...], ...]
    markdown: str


@dataclass(frozen=True)
class ParsedDocumentAnalysis:
    page_count: int
    analyzed_page_count: int
    tables: tuple[ParsedTable, ...]
    table_count_lower_bound: bool
    preview_page_num: int
    markdown: str = ""


@dataclass(frozen=True)
class RenderedPreview:
    page_num: int
    width: int
    height: int
    png_bytes: bytes


class LiteParseRuntime(Protocol):
    def parse(self, file_data: bytes | Path) -> ParseResult: ...

    def screenshot(
        self,
        file_path: Path,
        *,
        page_numbers: list[int] | None,
    ) -> list[ScreenshotResult]: ...

    def close(self) -> None: ...


def _page_ranges(page_numbers: Sequence[int]) -> str:
    """Format sorted page numbers as a LiteParse target-pages selector such as ``1-3,5``."""
    ranges: list[str] = []
    ordered = sorted(set(page_numbers))
    start = previous = ordered[0]
    for number in (*ordered[1:], None):
        if number is not None and number == previous + 1:
            previous = number
            continue
        ranges.append(str(start) if start == previous else f"{start}-{previous}")
        if number is not None:
            start = previous = number
    return ",".join(ranges)


def _markdown_cell(value: str) -> str:
    flattened = " ".join(value.splitlines())
    return flattened.replace("\\", "\\\\").replace("|", "\\|")


def _rectangular_rows(
    rows: Sequence[Sequence[str]],
) -> tuple[tuple[str, ...], ...]:
    width = max((len(row) for row in rows), default=0)
    if width == 0:
        return ()
    return tuple(tuple((*row, *("" for _ in range(width - len(row))))) for row in rows)


def markdown_table(rows: Sequence[Sequence[str]]) -> str:
    normalized = _rectangular_rows(rows)
    if not normalized:
        return ""
    header = normalized[0]
    body = normalized[1:]

    def render(row: Sequence[str]) -> str:
        return "| " + " | ".join(_markdown_cell(cell) for cell in row) + " |"

    return "\n".join(
        (
            render(header),
            render(tuple("---" for _ in header)),
            *(render(row) for row in body),
        )
    )


class LiteParseAdapter:
    def __init__(
        self,
        *,
        page_limit: int,
        parser_timeout_seconds: float,
        screenshot_dpi: float,
        parser_factory: Callable[..., LiteParseRuntime] | None = None,
        ocr_mode: OcrMode = "auto",
        ocr_min_page_chars: int = 25,
    ) -> None:
        if page_limit < 1 or page_limit > 1000:
            raise ValueError("The parser page limit must be between 1 and 1000.")
        if parser_timeout_seconds <= 0:
            raise ValueError("The parser timeout must be positive.")
        if screenshot_dpi <= 0:
            raise ValueError("The screenshot DPI must be positive.")
        if ocr_mode not in ("auto", "always", "never"):
            raise ValueError("The OCR mode must be auto, always, or never.")
        if ocr_min_page_chars < 1:
            raise ValueError("The OCR text threshold must be positive.")
        self._page_limit = page_limit
        self._parser_timeout_seconds = parser_timeout_seconds
        self._screenshot_dpi = screenshot_dpi
        self._ocr_mode = ocr_mode
        self._ocr_min_page_chars = ocr_min_page_chars
        self._parser_factory = parser_factory or cast(
            Callable[..., LiteParseRuntime],
            LiteParse,
        )

    def parse_pdf(self, file_data: bytes | Path) -> ParsedDocumentAnalysis:
        """Parse a PDF, running OCR only on pages that have no usable text layer.

        OCR dominates parse time (about 400 times the cost of text-layer extraction on a
        born-digital page), so ``auto`` mode first parses every page with OCR disabled and
        then re-parses only the pages that came back without text.
        """
        deadline = time.monotonic() + self._parser_timeout_seconds
        try:
            if self._ocr_mode != "auto":
                result = self._run(file_data, ocr=self._ocr_mode == "always", deadline=deadline)
                return self._map_pages(result.pages, result.total_pages, result.text)
            result = self._run(file_data, ocr=False, deadline=deadline)
            scanned = self._pages_without_text(result.pages)
            if not scanned:
                return self._map_pages(result.pages, result.total_pages, result.text)
            ocr_result = self._run(
                file_data,
                ocr=True,
                deadline=deadline,
                target_pages=_page_ranges(scanned),
            )
            return self._merge(result, ocr_result, set(scanned))
        except AnalysisParserError:
            raise
        except (ParseTimeoutError, TimeoutError) as error:
            raise AnalysisParserTimeoutError(
                "Document analysis exceeded the configured parser timeout."
            ) from error
        except Exception as error:
            raise AnalysisParserError("Document analysis could not parse the PDF.") from error

    def _run(
        self,
        file_data: bytes | Path,
        *,
        ocr: bool,
        deadline: float,
        target_pages: str | None = None,
    ) -> ParseResult:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AnalysisParserTimeoutError(
                "Document analysis exceeded the configured parser timeout."
            )
        options: dict[str, object] = {
            "max_pages": self._page_limit,
            "extract_blocks": True,
            "output_format": "markdown",
            "continue_on_page_error": True,
            "quiet": True,
            "num_workers": 1,
            "pool_size": 1,
            "parse_timeout": remaining,
            "ocr_enabled": ocr,
        }
        if target_pages is not None:
            options["target_pages"] = target_pages
        parser = self._parser_factory(**options)
        try:
            return parser.parse(file_data)
        finally:
            self._close_safely(parser)

    def _pages_without_text(self, pages: Sequence[ParsedPage]) -> list[int]:
        return [
            page.page_num
            for page in pages
            if len("".join((page.text or "").split())) < self._ocr_min_page_chars
        ]

    def _merge(
        self,
        text_pass: ParseResult,
        ocr_pass: ParseResult,
        scanned: set[int],
    ) -> ParsedDocumentAnalysis:
        recognised = {page.page_num: page for page in ocr_pass.pages if page.page_num in scanned}
        if set(recognised) != scanned:
            raise AnalysisParserError("Document analysis returned no text for scanned pages.")
        pages = [recognised.get(page.page_num, page) for page in text_pass.pages]
        markdown = LITEPARSE_PAGE_SEPARATOR.join(page.markdown or "" for page in pages)
        return self._map_pages(pages, text_pass.total_pages, markdown)

    def render_preview(self, file_path: Path, *, page_num: int) -> RenderedPreview:
        if page_num < 1:
            raise ValueError("Preview page numbers start at 1.")
        parser = self._parser_factory(
            dpi=self._screenshot_dpi,
            quiet=True,
            num_workers=1,
            pool_size=1,
            parse_timeout=self._parser_timeout_seconds,
        )
        try:
            screenshots = parser.screenshot(file_path, page_numbers=[page_num])
            if len(screenshots) != 1 or screenshots[0].page_num != page_num:
                raise AnalysisParserError("Document preview rendering returned no page.")
            screenshot = screenshots[0]
            if (
                screenshot.width <= 0
                or screenshot.height <= 0
                or not screenshot.image_bytes.startswith(b"\x89PNG\r\n\x1a\n")
            ):
                raise AnalysisParserError("Document preview rendering returned invalid PNG data.")
            return RenderedPreview(
                page_num=screenshot.page_num,
                width=screenshot.width,
                height=screenshot.height,
                png_bytes=screenshot.image_bytes,
            )
        except AnalysisParserError:
            raise
        except (ParseTimeoutError, TimeoutError) as error:
            raise AnalysisParserTimeoutError(
                "Document preview rendering exceeded the configured parser timeout."
            ) from error
        except Exception as error:
            raise AnalysisParserError("Document preview rendering failed.") from error
        finally:
            self._close_safely(parser)

    @staticmethod
    def _close_safely(parser: LiteParseRuntime) -> None:
        try:
            parser.close()
        except Exception:
            # Cleanup failures must not replace the sanitized operation result.
            return

    @staticmethod
    def _map_pages(
        pages: Sequence[ParsedPage],
        total_pages: int,
        markdown: str,
    ) -> ParsedDocumentAnalysis:
        if not pages:
            raise AnalysisParserError("Document analysis returned no pages.")
        tables: list[ParsedTable] = []
        for page in pages:
            page_tables = [block for block in (page.blocks or ()) if block.kind == "table"]
            for table_index, block in enumerate(page_tables):
                tables.append(LiteParseAdapter._map_table(page, table_index, block))
        page_count = max(total_pages, len(pages))
        return ParsedDocumentAnalysis(
            page_count=page_count,
            analyzed_page_count=len(pages),
            tables=tuple(tables),
            table_count_lower_bound=page_count > len(pages),
            preview_page_num=tables[0].page_num if tables else 1,
            markdown=markdown,
        )

    @staticmethod
    def _map_table(page: ParsedPage, table_index: int, block: LayoutBlock) -> ParsedTable:
        if block.bbox is None:
            raise AnalysisParserError("Document analysis returned invalid table geometry.")
        values = (block.bbox.x, block.bbox.y, block.bbox.width, block.bbox.height)
        if (
            not all(isfinite(value) for value in values)
            or block.bbox.x < 0
            or block.bbox.y < 0
            or block.bbox.width <= 0
            or block.bbox.height <= 0
        ):
            raise AnalysisParserError("Document analysis returned invalid table geometry.")
        bounds = TableBoundingBox(
            x=block.bbox.x,
            y=block.bbox.y,
            width=block.bbox.width,
            height=block.bbox.height,
        )
        tolerance = 0.5
        if (
            bounds.x + bounds.width > page.width + tolerance
            or bounds.y + bounds.height > page.height + tolerance
        ):
            raise AnalysisParserError("Document analysis returned invalid table geometry.")
        rows: list[tuple[str, ...]] = []
        if block.header is not None:
            rows.append(tuple(cell.text for cell in block.header))
        if block.rows is not None:
            rows.extend(tuple(cell.text for cell in row) for row in block.rows)
        cells = _rectangular_rows(rows)
        if not cells:
            raise AnalysisParserError("Document analysis returned an empty table block.")
        return ParsedTable(
            page_num=page.page_num,
            table_index=table_index,
            bounding_box=bounds,
            cells=cells,
            markdown=markdown_table(cells),
        )
