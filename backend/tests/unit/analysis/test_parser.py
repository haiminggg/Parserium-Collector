from pathlib import Path
from typing import Any

import pytest
from liteparse import (
    AnnotationRect,
    LayoutBlock,
    LayoutCell,
    ParsedPage,
    ParseResult,
    ScreenshotResult,
)

from parserium_collector.features.analysis.parser import (
    LITEPARSE_PAGE_SEPARATOR,
    AnalysisParserError,
    AnalysisParserTimeoutError,
    LiteParseAdapter,
    _page_ranges,
    markdown_table,
)


class DeterministicLiteParseTestDouble:
    def __init__(
        self,
        result: ParseResult,
        screenshots: list[ScreenshotResult],
        failure: Exception | None = None,
        close_failure: Exception | None = None,
    ) -> None:
        self.result = result
        self.screenshots = screenshots
        self.failure = failure
        self.close_failure = close_failure
        self.parsed: bytes | Path | None = None
        self.screenshot_path: Path | None = None
        self.screenshot_pages: list[int] | None = None
        self.closed = False

    def parse(self, file_data: bytes | Path) -> ParseResult:
        self.parsed = file_data
        if self.failure is not None:
            raise self.failure
        return self.result

    def screenshot(
        self,
        file_path: Path,
        *,
        page_numbers: list[int] | None,
    ) -> list[ScreenshotResult]:
        self.screenshot_path = file_path
        self.screenshot_pages = page_numbers
        if self.failure is not None:
            raise self.failure
        return self.screenshots

    def close(self) -> None:
        self.closed = True
        if self.close_failure is not None:
            raise self.close_failure


def table_result(*, total_pages: int = 2) -> ParseResult:
    table = LayoutBlock(
        kind="table",
        header=[
            LayoutCell("Fund", AnnotationRect(10, 20, 100, 20)),
            LayoutCell("NAV | USD", AnnotationRect(110, 20, 100, 20)),
        ],
        rows=[
            [
                LayoutCell("A", AnnotationRect(10, 40, 100, 20)),
                LayoutCell("$10", AnnotationRect(110, 40, 100, 20)),
            ],
            [LayoutCell("Empty", AnnotationRect(10, 60, 100, 20))],
        ],
        bbox=AnnotationRect(10, 20, 200, 60),
    )
    return ParseResult(
        pages=[
            ParsedPage(page_num=1, width=612, height=792, text="No table", blocks=[]),
            ParsedPage(
                page_num=2,
                width=612,
                height=792,
                text="Fund NAV",
                blocks=[table],
            ),
        ],
        text="No table\nFund NAV",
        total_pages=total_pages,
    )


def adapter_with(
    parser: DeterministicLiteParseTestDouble,
) -> tuple[LiteParseAdapter, dict[str, Any]]:
    config: dict[str, Any] = {}

    def factory(**kwargs: Any) -> DeterministicLiteParseTestDouble:
        config.update(kwargs)
        return parser

    return (
        LiteParseAdapter(
            page_limit=200,
            parser_timeout_seconds=120,
            screenshot_dpi=150,
            parser_factory=factory,
            ocr_mode="always",
        ),
        config,
    )


def test_parse_maps_table_blocks_cells_bounds_and_page_limit() -> None:
    parser = DeterministicLiteParseTestDouble(table_result(total_pages=240), [])
    adapter, config = adapter_with(parser)

    result = adapter.parse_pdf(b"deterministic validated PDF fixture")

    assert parser.parsed == b"deterministic validated PDF fixture"
    assert config["max_pages"] == 200
    assert config["extract_blocks"] is True
    assert config["parse_timeout"] == pytest.approx(120, abs=1)
    assert config["ocr_enabled"] is True
    assert config["pool_size"] == 1
    assert parser.closed is True
    assert result.page_count == 240
    assert result.analyzed_page_count == 2
    assert result.markdown == "No table\nFund NAV"
    assert result.table_count_lower_bound is True
    assert result.preview_page_num == 2
    assert len(result.tables) == 1
    assert result.tables[0].page_num == 2
    assert result.tables[0].bounding_box.model_dump() == {
        "x": 10.0,
        "y": 20.0,
        "width": 200.0,
        "height": 60.0,
    }
    assert result.tables[0].cells == (
        ("Fund", "NAV | USD"),
        ("A", "$10"),
        ("Empty", ""),
    )
    assert "NAV \\| USD" in result.tables[0].markdown


def test_markdown_table_escapes_pipes_flattens_lines_and_preserves_empty_cells() -> None:
    rendered = markdown_table((("Name", "Notes"), ("A | B", "line one\nline two"), ("", "")))

    assert rendered == ("| Name | Notes |\n| --- | --- |\n| A \\| B | line one line two |\n|  |  |")


def test_no_table_document_uses_first_page_for_preview() -> None:
    result = ParseResult(
        pages=[ParsedPage(page_num=1, width=612, height=792, text="Narrative", blocks=[])],
        text="Narrative",
        total_pages=1,
    )
    adapter, _ = adapter_with(DeterministicLiteParseTestDouble(result, []))

    parsed = adapter.parse_pdf(b"validated fixture")

    assert parsed.tables == ()
    assert parsed.preview_page_num == 1
    assert parsed.table_count_lower_bound is False


def test_invalid_table_bounds_fail_with_a_safe_parser_error() -> None:
    result = table_result()
    assert result.pages[1].blocks is not None
    assert result.pages[1].blocks[0].bbox is not None
    result.pages[1].blocks[0].bbox.x = -1
    adapter, _ = adapter_with(DeterministicLiteParseTestDouble(result, []))

    with pytest.raises(AnalysisParserError, match="invalid table geometry"):
        adapter.parse_pdf(b"validated fixture")


def test_preview_uses_requested_page_and_returns_real_png_dimensions(tmp_path: Path) -> None:
    source = tmp_path / "validated.pdf"
    source.write_bytes(b"deterministic fixture placeholder")
    screenshot = ScreenshotResult(
        page_num=2,
        width=1275,
        height=1650,
        image_bytes=b"\x89PNG\r\n\x1a\nfixture",
    )
    parser = DeterministicLiteParseTestDouble(table_result(), [screenshot])
    adapter, config = adapter_with(parser)

    preview = adapter.render_preview(source, page_num=2)

    assert config["dpi"] == 150
    assert parser.screenshot_path == source
    assert parser.screenshot_pages == [2]
    assert preview.page_num == 2
    assert preview.width == 1275
    assert preview.height == 1650
    assert preview.png_bytes.startswith(b"\x89PNG")
    assert parser.closed is True


@pytest.mark.parametrize(
    ("failure", "expected_type"),
    (
        (TimeoutError("test-only raw timeout path"), AnalysisParserTimeoutError),
        (RuntimeError("test-only raw parser path"), AnalysisParserError),
    ),
)
def test_parser_failures_are_sanitized(
    failure: Exception,
    expected_type: type[AnalysisParserError],
) -> None:
    parser = DeterministicLiteParseTestDouble(table_result(), [], failure=failure)
    adapter, _ = adapter_with(parser)

    with pytest.raises(expected_type) as captured:
        adapter.parse_pdf(b"validated fixture")

    assert "test-only" not in str(captured.value)
    assert "raw" not in str(captured.value)


def test_close_failure_does_not_mask_a_sanitized_parse_failure() -> None:
    parser = DeterministicLiteParseTestDouble(
        table_result(),
        [],
        failure=RuntimeError("test-only parse detail"),
        close_failure=RuntimeError("test-only close detail"),
    )
    adapter, _ = adapter_with(parser)

    with pytest.raises(AnalysisParserError, match="could not parse") as captured:
        adapter.parse_pdf(b"validated fixture")

    assert "test-only" not in str(captured.value)


def text_page(number: int, text: str, *, blocks: list[LayoutBlock] | None = None) -> ParsedPage:
    return ParsedPage(
        page_num=number,
        width=612,
        height=792,
        text=text,
        markdown=text,
        blocks=blocks if blocks is not None else [],
    )


def scripted_adapter(
    text_pass: ParseResult,
    ocr_pass: ParseResult | None = None,
    **adapter_options: Any,
) -> tuple[LiteParseAdapter, list[dict[str, Any]]]:
    calls: list[dict[str, Any]] = []
    doubles = {
        False: DeterministicLiteParseTestDouble(text_pass, []),
        True: DeterministicLiteParseTestDouble(ocr_pass or text_pass, []),
    }

    def factory(**kwargs: Any) -> DeterministicLiteParseTestDouble:
        calls.append(kwargs)
        return doubles[bool(kwargs["ocr_enabled"])]

    adapter = LiteParseAdapter(
        page_limit=200,
        parser_timeout_seconds=120,
        screenshot_dpi=150,
        parser_factory=factory,
        **adapter_options,
    )
    return adapter, calls


BODY = "A page with a real text layer that comfortably clears the threshold."


def test_auto_mode_skips_ocr_when_every_page_has_a_text_layer() -> None:
    text_pass = ParseResult(
        pages=[text_page(1, BODY), text_page(2, BODY)],
        text=f"{BODY}{LITEPARSE_PAGE_SEPARATOR}{BODY}",
        total_pages=2,
    )
    adapter, calls = scripted_adapter(text_pass)

    analysis = adapter.parse_pdf(b"validated fixture")

    assert [call["ocr_enabled"] for call in calls] == [False]
    assert "target_pages" not in calls[0]
    assert analysis.markdown == text_pass.text


def test_auto_mode_runs_ocr_only_on_pages_without_text_and_merges_in_order() -> None:
    text_pass = ParseResult(
        pages=[text_page(1, BODY), text_page(2, ""), text_page(3, BODY), text_page(4, "  \n ")],
        text="ignored for merged results",
        total_pages=4,
    )
    ocr_pass = ParseResult(
        pages=[text_page(2, "Recognised page two"), text_page(4, "Recognised page four")],
        text="ignored",
        total_pages=4,
    )
    adapter, calls = scripted_adapter(text_pass, ocr_pass)

    analysis = adapter.parse_pdf(b"validated fixture")

    assert [call["ocr_enabled"] for call in calls] == [False, True]
    assert calls[1]["target_pages"] == "2,4"
    assert analysis.markdown == LITEPARSE_PAGE_SEPARATOR.join(
        [BODY, "Recognised page two", BODY, "Recognised page four"]
    )
    assert analysis.page_count == 4
    assert analysis.analyzed_page_count == 4


def test_auto_mode_keeps_tables_found_on_recognised_pages() -> None:
    table = table_result().pages[1].blocks
    text_pass = ParseResult(
        pages=[text_page(1, BODY), text_page(2, "")],
        text="ignored",
        total_pages=2,
    )
    ocr_pass = ParseResult(
        pages=[text_page(2, "Fund NAV", blocks=table)],
        text="ignored",
        total_pages=2,
    )
    adapter, _ = scripted_adapter(text_pass, ocr_pass)

    analysis = adapter.parse_pdf(b"validated fixture")

    assert len(analysis.tables) == 1
    assert analysis.tables[0].page_num == 2
    assert analysis.preview_page_num == 2


def test_second_pass_receives_only_the_remaining_time_budget() -> None:
    text_pass = ParseResult(pages=[text_page(1, "")], text="", total_pages=1)
    ocr_pass = ParseResult(pages=[text_page(1, "Recognised")], text="", total_pages=1)
    adapter, calls = scripted_adapter(text_pass, ocr_pass)

    adapter.parse_pdf(b"validated fixture")

    assert calls[1]["parse_timeout"] <= calls[0]["parse_timeout"] <= 120


def test_auto_mode_fails_safely_when_ocr_returns_no_page_for_a_scanned_page() -> None:
    text_pass = ParseResult(pages=[text_page(1, BODY), text_page(2, "")], text="", total_pages=2)
    ocr_pass = ParseResult(pages=[], text="", total_pages=2)
    adapter, _ = scripted_adapter(text_pass, ocr_pass)

    with pytest.raises(AnalysisParserError, match="no text for scanned pages"):
        adapter.parse_pdf(b"validated fixture")


@pytest.mark.parametrize(("mode", "expected"), (("never", False), ("always", True)))
def test_fixed_ocr_modes_parse_once_with_ocr_forced(mode: str, expected: bool) -> None:
    result = ParseResult(pages=[text_page(1, "")], text="", total_pages=1)
    adapter, calls = scripted_adapter(result, ocr_mode=mode)

    adapter.parse_pdf(b"validated fixture")

    assert [call["ocr_enabled"] for call in calls] == [expected]


def test_invalid_ocr_options_are_rejected() -> None:
    with pytest.raises(ValueError, match="OCR mode"):
        LiteParseAdapter(
            page_limit=10,
            parser_timeout_seconds=5,
            screenshot_dpi=72,
            ocr_mode="sometimes",  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="OCR text threshold"):
        LiteParseAdapter(
            page_limit=10, parser_timeout_seconds=5, screenshot_dpi=72, ocr_min_page_chars=0
        )


@pytest.mark.parametrize(
    ("pages", "expected"),
    (([2], "2"), ([1, 2, 3], "1-3"), ([1, 2, 3, 5], "1-3,5"), ([7, 3, 4, 3], "3-4,7")),
)
def test_page_ranges_compress_consecutive_pages(pages: list[int], expected: str) -> None:
    assert _page_ranges(pages) == expected
