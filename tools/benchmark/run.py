"""Benchmark every registered parser engine against a corpus with expected answers.

Run from the repository root with the backend environment, for example:
    backend/.venv/Scripts/python tools/benchmark/run.py            (Windows)
    backend/.venv/bin/python tools/benchmark/run.py                (Linux)

Each engine runs in its own subprocess so cold-start time and peak memory are not shared.
Documents without an entry in the manifest are still timed but are not scored for accuracy.
"""

import argparse
import json
import re
import statistics
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent


def peak_memory_mb() -> float:
    """Peak resident memory of this process in MiB, or -1 when it cannot be read."""
    try:
        import resource  # Linux and macOS

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # ru_maxrss is KiB on Linux and bytes on macOS.
        return round(peak / (1024 if sys.platform.startswith("linux") else 1048576), 1)
    except ImportError:
        pass
    try:
        import psutil

        info = psutil.Process().memory_info()
        return round(getattr(info, "peak_wset", info.rss) / 1048576, 1)
    except Exception:  # noqa: BLE001
        return -1.0


def normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\\", "").replace("*", "")).strip().lower()


def markdown_tables(markdown: str) -> list[list[list[str]]]:
    tables: list[list[list[str]]] = []
    current: list[list[str]] = []
    for line in markdown.splitlines():
        if line.strip().startswith("|"):
            current.append(
                [normalise(cell) for cell in line.strip().strip("|").split("|")]
            )
        elif current:
            tables.append(current)
            current = []
    if current:
        tables.append(current)
    separator = re.compile(r":?-{2,}:?")
    return [
        [
            row
            for row in table
            if not all(separator.fullmatch(cell) or not cell for cell in row)
        ]
        for table in tables
    ]


def score(markdown: str, expected: dict) -> dict:
    tables = markdown_tables(markdown)
    text = normalise(markdown)
    table_cells = {normalise(cell) for table in tables for row in table for cell in row}
    headers_ok = cells = cells_in_tables = cells_in_text = 0
    for table in expected["tables"]:
        wanted = [normalise(header) for header in table["headers"]]
        if any(found and found[0][: len(wanted)] == wanted for found in tables):
            headers_ok += 1
        for value in (cell for row in table["rows"] for cell in row):
            cells += 1
            cells_in_tables += normalise(value) in table_cells
            cells_in_text += normalise(value) in text
    return {
        "tables_expected": len(expected["tables"]),
        "tables_found": len(tables),
        "headers_ok": headers_ok,
        "cells": cells,
        "cells_in_tables": cells_in_tables,
        "cells_in_text": cells_in_text,
    }


def load_manifest(corpus: Path) -> dict:
    path = corpus / "manifest.json"
    return (
        json.loads(path.read_text(encoding="utf-8"))["documents"]
        if path.is_file()
        else {}
    )


def work(engine_id: str, corpora: list[Path], page_limit: int, timeout: float) -> None:
    """Child process: run one engine over every PDF and print one JSON line per document."""
    from parserium_collector.features.analysis.engines import (
        EngineRequest,
        get_engine,
        has_extractable_text,
    )
    from pypdf import PdfReader

    engine = get_engine(engine_id)
    first = True
    for corpus in corpora:
        manifest = load_manifest(corpus)
        for pdf in sorted(corpus.glob("*.pdf")):
            expected = manifest.get(pdf.name)
            row: dict = {"engine": engine_id, "corpus": corpus.name, "file": pdf.name}
            try:
                pages = len(PdfReader(pdf).pages)
                begun = time.perf_counter()
                output = engine.parse(
                    EngineRequest(pdf, pages, max(page_limit, pages), timeout)
                )
                row.update(
                    pages=pages,
                    seconds=round(time.perf_counter() - begun, 2),
                    chars=len(output.markdown),
                    tables_reported=output.table_count,
                    has_text=has_extractable_text(output.markdown),
                    peak_mb=peak_memory_mb(),
                    scanned=bool(expected and expected.get("scanned")),
                )
                if expected is not None:
                    row.update(score(output.markdown, expected))
                out_dir = HERE / "results" / "markdown"
                out_dir.mkdir(parents=True, exist_ok=True)
                (out_dir / f"{engine_id}__{corpus.name}__{pdf.stem}.md").write_text(
                    output.markdown, encoding="utf-8"
                )
            except Exception as error:  # noqa: BLE001
                row["error"] = f"{type(error).__name__}: {str(error)[:140]}"
            row["first_document"] = first
            first = False
            print(json.dumps(row), flush=True)


def percent(part: int, whole: int) -> str:
    return f"{100 * part / whole:.0f}%" if whole else "n/a"


def summarise(rows: list[dict]) -> str:
    stamp = datetime.now(UTC).isoformat(timespec="seconds")
    lines = ["# Parser engine benchmark", "", f"Run at {stamp} on {sys.platform}.", ""]
    lines.append(
        "| Engine | Docs | Errors | First doc s | Median s | Max s | Peak MB | Headers "
        "| Cells in tables | Cells anywhere |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for engine in sorted({row["engine"] for row in rows}):
        mine = [row for row in rows if row["engine"] == engine]
        done = [row for row in mine if "error" not in row]
        scored = [row for row in done if "cells" in row]
        seconds = [row["seconds"] for row in done] or [0]
        headers = percent(
            sum(r["headers_ok"] for r in scored),
            sum(r["tables_expected"] for r in scored),
        )
        in_tables = percent(
            sum(r["cells_in_tables"] for r in scored), sum(r["cells"] for r in scored)
        )
        anywhere = percent(
            sum(r["cells_in_text"] for r in scored), sum(r["cells"] for r in scored)
        )
        peak = max((row["peak_mb"] for row in done), default=-1)
        first_row = next((row for row in done if row.get("first_document")), None)
        first_seconds = first_row["seconds"] if first_row else ""
        lines.append(
            f"| {engine} | {len(mine)} | {len(mine) - len(done)} | {first_seconds} "
            f"| {statistics.median(seconds):.2f} | {max(seconds):.2f} | {peak} "
            f"| {headers} | {in_tables} | {anywhere} |"
        )
    lines += ["", "## Per document", ""]
    lines += [
        "| Engine | Corpus | File | Pages | Seconds | Result |",
        "|---|---|---|---|---|---|",
    ]
    for row in rows:
        if "error" in row:
            result = f"error: {row['error']}"
        elif "cells" in row:
            result = (
                f"headers {row['headers_ok']}/{row['tables_expected']}, "
                f"tables found {row['tables_found']}, "
                f"cells in tables {row['cells_in_tables']}/{row['cells']}"
            )
            result += "" if row["has_text"] else ", NO TEXT"
        else:
            result = (
                f"{row['chars']} chars, {row['tables_reported']} tables (not scored)"
            )
            result += "" if row["has_text"] else ", NO TEXT"
        lines.append(
            f"| {row['engine']} | {row['corpus']} | {row['file']} | {row.get('pages', '')} "
            f"| {row.get('seconds', '')} | {result} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "--engines", nargs="*", help="Engine ids. Default: every registered one."
    )
    parser.add_argument(
        "--corpus", nargs="*", type=Path, help="PDF folders. Default: corpus, real."
    )
    parser.add_argument("--page-limit", type=int, default=50)
    parser.add_argument("--timeout", type=float, default=170.0)
    parser.add_argument("--worker", help=argparse.SUPPRESS)
    args = parser.parse_args()

    corpora = args.corpus or [
        path for path in (HERE / "corpus", HERE / "real") if path.is_dir()
    ]
    if args.worker:
        work(args.worker, corpora, args.page_limit, args.timeout)
        return 0
    if not any(list(path.glob("*.pdf")) for path in corpora):
        print(
            "No PDFs found. Run generate_corpus.py first or add files to tools/benchmark/real/."
        )
        return 2

    from parserium_collector.features.analysis.engines import engine_ids

    engines = args.engines or list(engine_ids())
    rows: list[dict] = []
    for engine in engines:
        print(f"== {engine}", flush=True)
        command = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--worker",
            engine,
            "--corpus",
            *map(str, corpora),
            "--page-limit",
            str(args.page_limit),
            "--timeout",
            str(args.timeout),
        ]
        done = subprocess.run(command, capture_output=True, text=True, check=False)
        rows += [
            json.loads(line)
            for line in done.stdout.splitlines()
            if line.startswith("{")
        ]
        if done.returncode != 0:
            failure = done.stderr.strip()[-200:]
            rows.append(
                {"engine": engine, "corpus": "-", "file": "-", "error": failure}
            )

    results = HERE / "results"
    results.mkdir(exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    lines = "\n".join(json.dumps(row) for row in rows)
    (results / f"{stamp}.jsonl").write_text(lines + "\n", encoding="utf-8")
    summary = summarise(rows)
    (results / f"{stamp}.md").write_text(summary, encoding="utf-8")
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
