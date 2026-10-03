"""Parser engine contract shared by the self-hosted and cloud editions.

An engine turns one validated document into Markdown. Every engine is bounded by the caller:
the cloud container runs it inside a subprocess with a hard wall-clock deadline, and the page
count has already been validated against the platform limit before an engine is invoked.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class UnknownEngineError(ValueError):
    """The requested parser engine is not registered."""


@dataclass(frozen=True)
class EngineInfo:
    id: str
    label: str
    description: str
    license: str
    ocr: bool
    # Rough peak memory on a typical document, used to decide which container tier can host it.
    memory_mb: int


@dataclass(frozen=True)
class EngineRequest:
    source: Path
    page_count: int
    page_limit: int
    timeout_seconds: float
    tessdata_path: str | None = None


@dataclass(frozen=True)
class EngineOutput:
    markdown: str
    page_count: int
    table_count: int


class ParserEngine(Protocol):
    @property
    def info(self) -> EngineInfo: ...

    def parse(self, request: EngineRequest) -> EngineOutput: ...


_STRUCTURE_ONLY_LINE = re.compile(r"^\s*(```[A-Za-z]*|-{3,})\s*$")


def has_extractable_text(markdown: str) -> bool:
    """Whether the output holds real text, ignoring empty code fences and page separators.

    LiteParse renders a page with no text as an empty ``text`` code block, so a plain
    ``strip()`` would wrongly treat a blank document as having content.
    """
    return any(
        line.strip() and not _STRUCTURE_ONLY_LINE.match(line) for line in markdown.splitlines()
    )


_SEPARATOR_ROW = re.compile(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


def count_markdown_tables(markdown: str) -> int:
    """Count Markdown pipe tables, defined by a header row followed by a separator row."""
    lines = markdown.splitlines()
    count = 0
    for index in range(1, len(lines)):
        current = lines[index].strip()
        previous = lines[index - 1].strip()
        if (
            current.startswith("|")
            and "-" in current
            and _SEPARATOR_ROW.match(current)
            and previous.startswith("|")
            and not _SEPARATOR_ROW.match(previous)
        ):
            count += 1
    return count
