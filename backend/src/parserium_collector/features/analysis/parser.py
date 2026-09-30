from collections.abc import Callable, Sequence
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from typing import Protocol, cast

from liteparse import (
    LiteParse,
    ParseResult,
    ParseTimeoutError,
    ScreenshotResult,
)
from liteparse.types import LayoutBlock, ParsedPage

from parserium_collector.features.analysis.models import TableBoundingBox


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
    ) -> None:
        if page_limit < 1 or page_limit > 1000:
            raise ValueError("The parser page limit must be between 1 and 1000.")
        if parser_timeout_seconds <= 0:
            raise ValueError("The parser timeout must be positive.")
        if screenshot_dpi <= 0:
            raise ValueError("The screenshot DPI must be positive.")
        self._page_limit = page_limit
        self._parser_timeout_seconds = parser_timeout_seconds
        self._screenshot_dpi = screenshot_dpi
        self._parser_factory = parser_factory or cast(
            Callable[..., LiteParseRuntime],
            LiteParse,
        )

    def parse_pdf(self, file_data: bytes | Path) -> ParsedDocumentAnalysis:
        parser = self._parser_factory(
            max_pages=self._page_limit,
            extract_blocks=True,
            output_format="markdown",
            continue_on_page_error=True,
            quiet=True,
            num_workers=1,
            pool_size=1,
            parse_timeout=self._parser_timeout_seconds,
        )
        try:
            result = parser.parse(file_data)
            return self._map_result(result)
        except AnalysisParserError:
            raise
        except (ParseTimeoutError, TimeoutError) as error:
            raise AnalysisParserTimeoutError(
                "Document analysis exceeded the configured parser timeout."
            ) from error
        except Exception as error:
            raise AnalysisParserError("Document analysis could not parse the PDF.") from error
        finally:
            self._close_safely(parser)

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
    def _map_result(result: ParseResult) -> ParsedDocumentAnalysis:
        if not result.pages:
            raise AnalysisParserError("Document analysis returned no pages.")
        tables: list[ParsedTable] = []
        for page in result.pages:
            page_tables = [block for block in (page.blocks or ()) if block.kind == "table"]
            for table_index, block in enumerate(page_tables):
                tables.append(LiteParseAdapter._map_table(page, table_index, block))
        page_count = max(result.total_pages, len(result.pages))
        return ParsedDocumentAnalysis(
            page_count=page_count,
            analyzed_page_count=len(result.pages),
            tables=tuple(tables),
            table_count_lower_bound=page_count > len(result.pages),
            preview_page_num=tables[0].page_num if tables else 1,
            markdown=result.text,
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
