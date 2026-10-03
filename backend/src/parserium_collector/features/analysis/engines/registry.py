"""Registry of the parser engines users can choose between.

Engine modules are imported lazily so that listing engines, or running one of them, never loads
the heavy libraries of the others. The ids are part of the public API and are stored on parse
jobs, so they must never be renamed. The Cloud Worker keeps a matching allowlist in
``apps/cloud/src/engines.mjs``; a contract test fails if the two lists drift apart.
"""

from collections.abc import Callable

from parserium_collector.features.analysis.engines.base import (
    EngineInfo,
    ParserEngine,
    UnknownEngineError,
)
from parserium_collector.features.analysis.engines.liteparse_engine import (
    INFO as LITEPARSE_INFO,
)
from parserium_collector.features.analysis.engines.markitdown_engine import (
    INFO as MARKITDOWN_INFO,
)

DEFAULT_ENGINE = "liteparse"


def _liteparse() -> ParserEngine:
    from parserium_collector.features.analysis.engines.liteparse_engine import LiteParseEngine

    return LiteParseEngine()


def _markitdown() -> ParserEngine:
    from parserium_collector.features.analysis.engines.markitdown_engine import MarkItDownEngine

    return MarkItDownEngine()


_FACTORIES: dict[str, Callable[[], ParserEngine]] = {
    LITEPARSE_INFO.id: _liteparse,
    MARKITDOWN_INFO.id: _markitdown,
}
_INFOS: dict[str, EngineInfo] = {info.id: info for info in (LITEPARSE_INFO, MARKITDOWN_INFO)}


def engine_ids() -> tuple[str, ...]:
    return tuple(_FACTORIES)


def engine_infos() -> tuple[EngineInfo, ...]:
    return tuple(_INFOS.values())


def get_engine(engine_id: str) -> ParserEngine:
    factory = _FACTORIES.get(engine_id)
    if factory is None:
        raise UnknownEngineError("The requested parser engine is not available.")
    return factory()
