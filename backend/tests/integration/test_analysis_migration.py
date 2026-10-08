from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from parserium_collector.settings import Settings

ANALYSIS_TABLES = {
    "discovery_analysis_sessions",
    "candidate_analyses",
    "candidate_tables",
}


def test_document_analysis_migration_upgrades_and_downgrades() -> None:
    backend_root = Path(__file__).resolve().parents[2]
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "migrations"))

    command.upgrade(config, "0004_document_analysis")
    engine = create_engine(Settings().database_url())
    try:
        inspector = inspect(engine)
        assert ANALYSIS_TABLES <= set(inspector.get_table_names())
        candidate_foreign_keys = {
            constrained
            for foreign_key in inspector.get_foreign_keys("candidate_analyses")
            for constrained in foreign_key["constrained_columns"]
        }
        assert candidate_foreign_keys == {"session_id", "promoted_document_id"}

        command.downgrade(config, "0003_document_acquisition")
        inspector = inspect(engine)
        assert ANALYSIS_TABLES.isdisjoint(inspector.get_table_names())
    finally:
        engine.dispose()
