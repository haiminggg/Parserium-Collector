import json
import subprocess
import sys
from pathlib import Path

from pypdf import PdfWriter


ROOT = Path(__file__).resolve().parents[3]
RUNNER = ROOT / "apps" / "cloud" / "container" / "runner.py"
RULED_TABLE = ROOT / "tests" / "fixtures" / "analysis" / "ruled-table.pdf"


def run_runner(mode: str, source: Path) -> tuple[int, dict[str, object], str]:
    completed = subprocess.run(
        [sys.executable, str(RUNNER), mode, str(source)],
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
