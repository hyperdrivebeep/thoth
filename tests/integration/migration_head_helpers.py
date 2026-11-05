"""Current single Alembic head and its immediate predecessor for migration tests."""

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[2]
CURRENT_HEAD = "9f4c2d7e1a35"
PREVIOUS_HEAD = "8e3b5f1a7c42"


def current_single_head() -> str:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    scripts = ScriptDirectory.from_config(config)
    assert scripts.get_heads() == [CURRENT_HEAD]
    revision = scripts.get_revision(CURRENT_HEAD)
    assert revision is not None and revision.down_revision == PREVIOUS_HEAD
    return CURRENT_HEAD
