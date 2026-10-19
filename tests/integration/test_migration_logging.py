"""Opening the runtime's database must not disable an already imported thoth logger."""

import io
import logging
from pathlib import Path

from pytest import MonkeyPatch

from thoth.adapters.storage.alembic_migrations import migrate_sqlite_database


def test_runtime_migration_keeps_preexisting_thoth_warning_enabled(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.delenv("THOTH_ALEMBIC_URL", raising=False)
    logger = logging.getLogger("thoth.migration_logging_regression")
    assert logger.disabled is False

    migrate_sqlite_database(tmp_path / "workspace" / "db" / "thoth.sqlite3")

    assert logger.disabled is False
    # fileConfig replaces root handlers. Attach a fresh observer after that normal
    # configuration step; do not re-enable the logger or alter its effective level.
    output = io.StringIO()
    observer = logging.StreamHandler(output)
    logger.addHandler(observer)
    try:
        logger.warning("THOTH_MIGRATION_LOGGER_SURVIVED")
        assert output.getvalue() == "THOTH_MIGRATION_LOGGER_SURVIVED\n"
    finally:
        logger.removeHandler(observer)
        observer.close()
        output.close()
