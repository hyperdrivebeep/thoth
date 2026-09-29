"""Current single Alembic head and its immediate predecessor for migration tests."""

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[2]
CURRENT_HEAD = "3b06e4f87d92"
PREVIOUS_HEAD = "2af5d3e76c81"


def current_single_head() -> str:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    scripts = ScriptDirectory.from_config(config)
    assert scripts.get_heads() == [CURRENT_HEAD]
    revision = scripts.get_revision(CURRENT_HEAD)
    assert revision is not None and revision.down_revision == PREVIOUS_HEAD
    return CURRENT_HEAD
