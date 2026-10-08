"""LiteParse engine: layout-aware extraction with OCR limited to pages that need it."""

from functools import partial
from typing import Any

from parserium_collector.features.analysis.engines.base import (
    EngineInfo,
    EngineOutput,
    EngineRequest,
)
from parserium_collector.features.analysis.parser import LiteParseAdapter

INFO = EngineInfo(
    id="liteparse",
    label="LiteParse",
    description=(
        "Layout-aware extraction with table detection. Runs OCR only on pages without a "
        "text layer, so scanned pages are readable but their tables are returned as text."
    ),
    license="Apache-2.0",
    ocr=True,
    memory_mb=300,
)


class LiteParseEngine:
    @property
    def info(self) -> EngineInfo:
        return INFO

    def parse(self, request: EngineRequest) -> EngineOutput:
        from liteparse import LiteParse

        factory: Any = (
            partial(LiteParse, tessdata_path=request.tessdata_path)
            if request.tessdata_path
            else LiteParse
        )
        analysis = LiteParseAdapter(
            page_limit=request.page_limit,
            parser_timeout_seconds=request.timeout_seconds,
            screenshot_dpi=72,
            parser_factory=factory,
        ).parse_pdf(request.source)
        return EngineOutput(
            markdown=analysis.markdown,
            page_count=analysis.page_count,
            table_count=len(analysis.tables),
        )
