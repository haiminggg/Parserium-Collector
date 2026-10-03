from pathlib import Path

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from parserium_collector.features.analysis.engines import (
    DEFAULT_ENGINE,
    EngineRequest,
    UnknownEngineError,
    count_markdown_tables,
    engine_ids,
    engine_infos,
    get_engine,
    has_extractable_text,
)
from parserium_collector.features.analysis.parser import (
    LITEPARSE_PAGE_SEPARATOR,
    LiteParseAdapter,
)

# Copies of tests/fixtures/analysis, because the backend image only ships backend/tests.
FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "analysis"
RULED = FIXTURES / "ruled-table.pdf"
BORDERLESS = FIXTURES / "borderless-table.pdf"
FUNDS = ("Alpha Income", "Beta Growth", "10.25", "22.10")


def request_for(source: Path, *, pages: int = 1) -> EngineRequest:
    return EngineRequest(source=source, page_count=pages, page_limit=20, timeout_seconds=60)


def write_text_pdf(path: Path, page_texts: list[str]) -> None:
    writer = PdfWriter()
    font = writer._add_object(
        DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
    )
    for text in page_texts:
        page = writer.add_blank_page(width=612, height=792)
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
        )
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 72 700 Td ({text}) Tj ET".encode())
        page[NameObject("/Contents")] = writer._add_object(stream)
    with path.open("wb") as handle:
        writer.write(handle)


def test_registry_lists_engines_with_a_stable_default() -> None:
    assert engine_ids() == ("liteparse", "markitdown")
    assert DEFAULT_ENGINE == "liteparse"
    assert {info.id for info in engine_infos()} == set(engine_ids())
    assert all(info.label and info.description and info.license for info in engine_infos())


def test_registry_rejects_unknown_engines_without_echoing_the_name() -> None:
    with pytest.raises(UnknownEngineError) as captured:
        get_engine("../../etc/passwd")

    assert "passwd" not in str(captured.value)


def test_only_liteparse_declares_ocr() -> None:
    assert {info.id for info in engine_infos() if info.ocr} == {"liteparse"}


@pytest.mark.parametrize(
    ("markdown", "expected"),
    (
        ("", 0),
        ("plain paragraph", 0),
        ("| A | B |\n| --- | --- |\n| 1 | 2 |", 1),
        ("| A | B |\n|---|---|\n| 1 | 2 |\n\ntext\n\n| C |\n|:---|\n| 3 |", 2),
        ("| not | a table |\n| 1 | 2 |", 0),
        ("| --- | --- |", 0),
    ),
)
def test_count_markdown_tables(markdown: str, expected: int) -> None:
    assert count_markdown_tables(markdown) == expected


@pytest.mark.parametrize("engine_id", ["liteparse", "markitdown"])
def test_every_engine_extracts_the_ruled_table_fixture(engine_id: str) -> None:
    output = get_engine(engine_id).parse(request_for(RULED))

    assert output.page_count == 1
    assert output.table_count == 1
    assert all(value in output.markdown for value in FUNDS)
    header = next(line for line in output.markdown.splitlines() if line.startswith("|"))
    assert [cell.strip() for cell in header.strip("|").split("|")] == ["Fund", "NAV USD", "Return"]


@pytest.mark.parametrize("engine_id", ["markitdown"])
def test_text_layer_engines_find_the_borderless_table(engine_id: str) -> None:
    output = get_engine(engine_id).parse(request_for(BORDERLESS))

    assert output.table_count == 1
    assert all(value in output.markdown for value in FUNDS)


# MarkItDown sniffs content and echoes non-PDF bytes back as text, so it relies on the caller
# validating the PDF first. The container runner does, and its tests cover every engine.
@pytest.mark.parametrize("engine_id", ["liteparse"])
def test_engines_never_invent_content_from_unreadable_input(engine_id: str, tmp_path: Path) -> None:
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.7 this is not a document")

    try:
        output = get_engine(engine_id).parse(request_for(broken))
    except Exception as error:  # noqa: BLE001
        assert str(broken) not in str(error)
    else:
        assert not has_extractable_text(output.markdown)
        assert output.table_count == 0


def test_liteparse_page_separator_matches_the_installed_parser(tmp_path: Path) -> None:
    source = tmp_path / "two-pages.pdf"
    write_text_pdf(source, ["First page has real text content", "Second page also has text"])
    adapter = LiteParseAdapter(
        page_limit=20, parser_timeout_seconds=120, screenshot_dpi=72, ocr_mode="never"
    )

    analysis = adapter.parse_pdf(source)

    assert analysis.page_count == 2
    assert LITEPARSE_PAGE_SEPARATOR in analysis.markdown
    first, second = analysis.markdown.split(LITEPARSE_PAGE_SEPARATOR)
    assert "First page" in first
    assert "Second page" in second


def test_auto_mode_matches_forced_ocr_output_on_a_text_layer_document(tmp_path: Path) -> None:
    source = tmp_path / "text-layer.pdf"
    write_text_pdf(source, ["Quarterly commentary with an ordinary text layer", "Second page text"])
    options = {"page_limit": 20, "parser_timeout_seconds": 120, "screenshot_dpi": 72}

    automatic = LiteParseAdapter(**options, ocr_mode="auto").parse_pdf(source)
    forced = LiteParseAdapter(**options, ocr_mode="always").parse_pdf(source)

    assert automatic.markdown == forced.markdown
    assert automatic.page_count == forced.page_count


@pytest.mark.parametrize(
    ("markdown", "expected"),
    (
        ("", False),
        ("   \n\n", False),
        ("```text\n\n```", False),
        ("```text\n\n```\n\n-----\n\n```text\n\n```", False),
        ("Some real text", True),
        ("```text\nreal text inside a fence\n```", True),
        ("| A |\n|---|\n| 1 |", True),
    ),
)
def test_has_extractable_text_ignores_empty_fences_and_separators(
    markdown: str, expected: bool
) -> None:
    assert has_extractable_text(markdown) is expected
