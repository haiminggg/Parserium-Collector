# Parser engine benchmark

Scores every engine in the registry (`backend/src/parserium_collector/features/analysis/engines/`)
on speed, memory and table accuracy, so engine and version changes are decided with numbers.
A new engine added to the registry is picked up automatically.

## Run it

From the repository root:

```
# 1. Build the synthetic corpus (needs PyMuPDF, used only by this script)
uv run --with pymupdf python tools/benchmark/generate_corpus.py

# 2. Benchmark every registered engine with the backend environment
backend/.venv/Scripts/python tools/benchmark/run.py      # Windows
backend/.venv/bin/python tools/benchmark/run.py          # Linux
```

Options: `--engines liteparse markitdown`, `--corpus DIR ...`, `--page-limit`, `--timeout`.

Results land in `tools/benchmark/results/` as a Markdown summary, a JSON Lines file with one row per
engine and document, and the extracted Markdown for each run.

## What is measured

| Column | Meaning |
|---|---|
| First doc s | Time for the first document, which includes loading the engine, so it approximates a cold start |
| Median s, Max s | Parse time per document across the corpus |
| Peak MB | Peak memory of the engine process, used to choose a container tier |
| Headers | Expected table header rows found as the first row of a Markdown table |
| Cells in tables | Expected cell values found inside a Markdown table |
| Cells anywhere | Expected cell values found anywhere in the output, tables or prose |
| NO TEXT | The output holds no real text, which is what an engine does on a scan it cannot read |

## Matching production

Cloudflare staging runs the parser at one quarter of a vCPU and 1 GiB. Numbers from a laptop are
useful for comparing engines with each other but not for predicting production time. For that,
run the benchmark inside the backend image with the same limits:

```
docker build --target backend-test --tag parserium-benchmark .
docker run --rm --cpus 0.25 --memory 1g --volume "$PWD:/repo" --workdir /repo \
  parserium-benchmark /app/.venv/bin/python tools/benchmark/run.py
```

## Using real documents

Put PDFs in `tools/benchmark/real/`. They are Git-ignored, so private documents never reach the
repository. Without a manifest they are timed but not scored. To score them, add
`tools/benchmark/real/manifest.json`:

```
{"documents": {"annual-report.pdf": {"pages": 40, "scanned": false,
  "tables": [{"headers": ["Item", "2024"], "rows": [["Revenue", "412.8"]]}]}}}
```

The synthetic corpus is generated with known answers and is not a substitute for real documents.
Do not change the default engine or an engine version on synthetic results alone.
