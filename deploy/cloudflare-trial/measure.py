"""Compatibility experiment using a real repository fixture, not customer data."""
import hashlib
import importlib.metadata
import json
import time
from functools import partial
from pathlib import Path

from liteparse import LiteParse
from parserium_collector.features.analysis.parser import LiteParseAdapter

fixture = Path('/trial/ruled-table.pdf')
started = time.perf_counter()
result = LiteParseAdapter(
    page_limit=5, parser_timeout_seconds=45, screenshot_dpi=72,
    parser_factory=partial(LiteParse, tessdata_path='/trial/tessdata'),
).parse_pdf(fixture)
peak = Path('/sys/fs/cgroup/memory.peak')
print(json.dumps({
    'fixture': fixture.name,
    'fixture_sha256': hashlib.sha256(fixture.read_bytes()).hexdigest(),
    'liteparse_version': importlib.metadata.version('liteparse'),
    'pages': result.page_count,
    'tables': len(result.tables),
    'markdown': [table.markdown for table in result.tables],
    'parse_seconds': round(time.perf_counter() - started, 3),
    'container_peak_memory_bytes': int(peak.read_text()) if peak.exists() else None,
}))
