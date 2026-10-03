"""Build the synthetic benchmark corpus, with a manifest of the expected answers.

This is test data only. It needs PyMuPDF, which is a developer tool for this script and not a
Parserium dependency. Run it with:
    uv run --with pymupdf python tools/benchmark/generate_corpus.py
"""

import json
import shutil
from pathlib import Path

import pymupdf as fitz

HERE = Path(__file__).resolve().parent
OUT = HERE / "corpus"
FIXTURES = HERE.parents[1] / "tests" / "fixtures" / "analysis"

SMALL_HEADERS = ["Fund", "NAV USD", "Return"]
SMALL_ROWS = [
    ["Alpha Income", "10.25", "4.8 percent"],
    ["Beta Growth", "22.10", "7.2 percent"],
]
LOREM = (
    "The committee reviewed the quarterly position and noted that operating costs remained "
    "within the approved envelope. Members asked for a further breakdown of regional income "
    "and agreed to revisit the assumptions at the next scheduled meeting."
)

documents: dict[str, dict] = {}


def draw_table(page, x, y, headers, rows, col_w, row_h=22, ruled=True):
    columns = len(headers)
    width, height = col_w * columns, row_h * (len(rows) + 1)
    if ruled:
        for r in range(len(rows) + 2):
            page.draw_line((x, y + r * row_h), (x + width, y + r * row_h), width=0.8)
        for c in range(columns + 1):
            page.draw_line((x + c * col_w, y), (x + c * col_w, y + height), width=0.8)
    for c, header in enumerate(headers):
        page.insert_text(
            (x + c * col_w + 6, y + 15), header, fontsize=10, fontname="hebo"
        )
    for r, row in enumerate(rows, start=1):
        for c, value in enumerate(row):
            page.insert_text(
                (x + c * col_w + 6, y + r * row_h + 15),
                value,
                fontsize=10,
                fontname="helv",
            )
    return y + height


def report(pages: int = 12) -> None:
    doc = fitz.open()
    tables = []
    for number in range(1, pages + 1):
        page = doc.new_page(width=612, height=792)
        page.insert_text(
            (72, 80),
            f"Section {number}: Financial review",
            fontsize=18,
            fontname="hebo",
        )
        y = 110
        for _ in range(3):
            page.insert_textbox(
                fitz.Rect(72, y, 540, y + 70), LOREM, fontsize=10.5, fontname="helv"
            )
            y += 75
        if number % 2 == 0:
            headers = ["Item", "2024", "2025", "Change"]
            rows = [
                ["Total income", f"{400 + number}.6", f"{450 + number}.1", "+12.4%"],
                ["Operating costs", f"{300 + number}.2", f"{320 + number}.9", "+7.1%"],
                ["Net result", f"{100 + number}.4", f"{130 + number}.2", "+30.1%"],
            ]
            ruled = number != 8  # page 8 carries a borderless table
            draw_table(page, 72, y + 20, headers, rows, 117, ruled=ruled)
            tables.append(
                {"page": number, "headers": headers, "rows": rows, "ruled": ruled}
            )
        page.insert_text((290, 760), f"Page {number}", fontsize=9, fontname="helv")
    name = "report-12p-tables.pdf"
    doc.save(OUT / name)
    documents[name] = {"pages": pages, "scanned": False, "tables": tables}


def scan(pages: int = 3) -> None:
    source = fitz.open()
    tables = []
    for number in range(1, pages + 1):
        page = source.new_page(width=612, height=792)
        page.insert_text(
            (72, 80), f"Scanned notice {number}", fontsize=20, fontname="hebo"
        )
        page.insert_textbox(
            fitz.Rect(72, 110, 540, 260),
            LOREM + " " + LOREM,
            fontsize=12,
            fontname="helv",
        )
        draw_table(page, 72, 300, SMALL_HEADERS, SMALL_ROWS, 150)
        tables.append(
            {
                "page": number,
                "headers": SMALL_HEADERS,
                "rows": SMALL_ROWS,
                "ruled": True,
            }
        )
    out = fitz.open()
    for page in source:
        picture = page.get_pixmap(dpi=150)
        target = out.new_page(width=612, height=792)
        target.insert_image(target.rect, stream=picture.tobytes("jpeg", jpg_quality=70))
    name = "scan-3p-image-only.pdf"
    out.save(OUT / name)
    documents[name] = {"pages": pages, "scanned": True, "tables": tables}


def small_fixtures() -> None:
    """The repository's tiny single-page fixtures, copied so the corpus is self-contained."""
    wanted = {
        "ruled-table.pdf": [
            {"page": 1, "headers": SMALL_HEADERS, "rows": SMALL_ROWS, "ruled": True}
        ],
        "borderless-table.pdf": [
            {"page": 1, "headers": SMALL_HEADERS, "rows": SMALL_ROWS, "ruled": False}
        ],
        "no-table.pdf": [],
    }
    for name, tables in wanted.items():
        shutil.copyfile(FIXTURES / name, OUT / name)
        documents[name] = {"pages": 1, "scanned": False, "tables": tables}


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    report()
    scan()
    small_fixtures()
    (OUT / "manifest.json").write_text(
        json.dumps({"documents": documents}, indent=2), encoding="utf-8"
    )
    for pdf in sorted(OUT.glob("*.pdf")):
        print(f"{pdf.name}: {pdf.stat().st_size} bytes")
