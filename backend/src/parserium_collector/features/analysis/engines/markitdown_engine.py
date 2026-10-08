"""MarkItDown engine: lightweight, MIT-licensed text-layer extraction."""

from parserium_collector.features.analysis.engines.base import (
    EngineInfo,
    EngineOutput,
    EngineRequest,
    count_markdown_tables,
)
from parserium_collector.features.analysis.parser import AnalysisParserError

INFO = EngineInfo(
    id="markitdown",
    label="MarkItDown",
    description=(
        "Lightweight, fast extraction for documents with a text layer. Detects simple tables. "
        "No OCR, so scanned pages produce no text."
    ),
    license="MIT",
    ocr=False,
    memory_mb=120,
)


class MarkItDownEngine:
    @property
    def info(self) -> EngineInfo:
        return INFO

    def parse(self, request: EngineRequest) -> EngineOutput:
        try:
            from markitdown import MarkItDown

            markdown = MarkItDown(enable_plugins=False).convert(str(request.source)).text_content
        except Exception as error:
            raise AnalysisParserError("Document analysis could not parse the PDF.") from error
        return EngineOutput(
            markdown=markdown,
            page_count=request.page_count,
            table_count=count_markdown_tables(markdown),
        )
