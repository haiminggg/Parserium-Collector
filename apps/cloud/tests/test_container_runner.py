import json
import subprocess
import sys
from pathlib import Path

import pytest
from pypdf import PdfWriter


ROOT = Path(__file__).resolve().parents[3]
RUNNER = ROOT / "apps" / "cloud" / "container" / "runner.py"
RULED_TABLE = ROOT / "tests" / "fixtures" / "analysis" / "ruled-table.pdf"


def run_runner(
    mode: str, source: Path, engine: str | None = None
) -> tuple[int, dict[str, object], str]:
    completed = subprocess.run(
        [sys.executable, str(RUNNER), mode, str(source), *([engine] if engine else [])],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return completed.returncode, json.loads(completed.stdout), completed.stderr


def write_pdf(path: Path, *, pages: int, encrypted: bool = False) -> None:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    if encrypted:
        writer.encrypt("test-only-password")
    with path.open("wb") as stream:
        writer.write(stream)


def test_validate_accepts_real_pdf_and_reports_literal_page_count() -> None:
    returncode, result, stderr = run_runner("validate", RULED_TABLE)

    assert returncode == 0
    assert result == {"ok": True, "pageCount": 1}
    assert stderr == ""


# Known bug: on Linux LiteParse emits the header row as plain text above the table instead of as the
# first table row, and Windows does not. Strict, so fixing it fails this test and removes the marker.
@pytest.mark.xfail(
    sys.platform.startswith("linux"),
    reason="LiteParse puts the ruled-table header row outside the table on Linux",
    strict=True,
)
def test_parse_returns_real_ruled_table_markdown() -> None:
    returncode, result, stderr = run_runner("parse", RULED_TABLE)

    assert returncode == 0
    assert result["ok"] is True
    assert result["pageCount"] == 1
    assert result["tableCount"] == 1
    assert result["markdown"] == (
        "| Fund | NAV USD | Return |\n"
        "|---|---|---|\n"
        "| Alpha Income | 10.25 | 4.8 percent |\n"
        "| Beta Growth | 22.10 | 7.2 percent |"
    )
    assert isinstance(result["runtimeMs"], int)
    assert 0 < result["runtimeMs"] <= 60_000
    assert stderr == ""


def test_validate_rejects_structurally_invalid_pdf_without_internal_detail(
    tmp_path: Path,
) -> None:
    source = tmp_path / "invalid.pdf"
    source.write_bytes(b"%PDF-not-structurally-valid")

    returncode, result, stderr = run_runner("validate", source)

    assert returncode == 0
    assert result == {"ok": False, "error": "invalid_pdf", "retryable": False}
    assert stderr == ""


def test_validate_rejects_encrypted_pdf(tmp_path: Path) -> None:
    source = tmp_path / "encrypted.pdf"
    write_pdf(source, pages=1, encrypted=True)

    returncode, result, stderr = run_runner("validate", source)

    assert returncode == 0
    assert result == {"ok": False, "error": "encrypted_pdf", "retryable": False}
    assert stderr == ""


def test_validate_rejects_more_than_twenty_pages(tmp_path: Path) -> None:
    source = tmp_path / "too-many-pages.pdf"
    write_pdf(source, pages=21)

    returncode, result, stderr = run_runner("validate", source)

    assert returncode == 0
    assert result == {
        "ok": False,
        "error": "page_limit_exceeded",
        "retryable": False,
    }
    assert stderr == ""


ENGINES = ["liteparse", "markitdown"]


def test_parse_defaults_to_liteparse_and_reports_the_engine() -> None:
    _, result, _ = run_runner("parse", RULED_TABLE)

    assert result["ok"] is True
    assert result["engine"] == "liteparse"


import pytest  # noqa: E402


@pytest.mark.parametrize("engine", ENGINES)
def test_every_engine_parses_the_ruled_table_through_the_runner(engine: str) -> None:
    returncode, result, stderr = run_runner("parse", RULED_TABLE, engine)

    assert returncode == 0
    assert result["ok"] is True
    assert result["engine"] == engine
    assert result["pageCount"] == 1
    assert result["tableCount"] == 1
    markdown = str(result["markdown"])
    assert all(value in markdown for value in ("Alpha Income", "10.25", "Beta Growth", "22.10"))
    assert 0 < int(result["runtimeMs"]) <= 60_000  # type: ignore[call-overload]
    assert stderr == ""


@pytest.mark.parametrize("engine", ["", "docling", "../runner", "LITEPARSE"])
def test_parse_rejects_unknown_engines_without_running_anything(
    engine: str, tmp_path: Path
) -> None:
    returncode, result, stderr = run_runner("parse", RULED_TABLE, engine or "unregistered")

    assert returncode == 0
    assert result == {"ok": False, "error": "invalid_request", "retryable": False}
    assert stderr == ""


@pytest.mark.parametrize("engine", ENGINES)
def test_a_document_without_text_fails_instead_of_returning_empty_markdown(
    engine: str, tmp_path: Path
) -> None:
    source = tmp_path / "blank.pdf"
    write_pdf(source, pages=1)

    returncode, result, stderr = run_runner("parse", source, engine)

    assert returncode == 0
    assert result["ok"] is False
    assert result["error"] == "no_text_extracted"
    assert result["retryable"] is False
    assert result["engine"] == engine
    assert "markdown" not in result
    assert stderr == ""


def test_validate_ignores_the_engine_argument() -> None:
    _, result, _ = run_runner("validate", RULED_TABLE, "markitdown")

    assert result == {"ok": True, "pageCount": 1}


@pytest.mark.parametrize("engine", ENGINES)
def test_a_corrupt_pdf_is_rejected_before_any_engine_runs(engine: str, tmp_path: Path) -> None:
    source = tmp_path / "corrupt.pdf"
    source.write_bytes(b"%PDF-1.7 this is not a document")

    returncode, result, stderr = run_runner("parse", source, engine)

    assert returncode == 0
    assert result == {"ok": False, "error": "invalid_pdf", "retryable": False}
    assert stderr == ""
