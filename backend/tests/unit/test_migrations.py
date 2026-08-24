from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def test_foundation_migration_is_the_only_schema_head() -> None:
    backend_root = Path(__file__).resolve().parents[2]
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "migrations"))

    scripts = ScriptDirectory.from_config(config)

    assert scripts.get_heads() == ["0001_foundation"]
