import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config
from pytest import MonkeyPatch


def test_prior_head_upgrade_and_downgrade_preserve_canonical_rows(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    db = tmp_path / "upgrade.sqlite3"
    monkeypatch.setenv("THOTH_ALEMBIC_URL", "sqlite+pysqlite:///" + db.as_posix())
    config = Config("alembic.ini")
    command.upgrade(config, "4c17f5089ea3")
    with sqlite3.connect(db) as conn:
        before = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'evidence_spans'"
        ).fetchone()
        conn.execute(
            "INSERT INTO projects (project_id,name,description,cutoff_at,lifecycle,overlay,"
            "policy_ref,revision,created_at) "
            "VALUES ('project:upgrade','Synthetic','','2026-10-01','DRAFT','general-rnd',"
            "'policy:upgrade',0,'2026-10-01')"
        )
        project = conn.execute("SELECT * FROM projects").fetchall()
    command.upgrade(config, "head")
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT * FROM projects").fetchall() == project
        assert (
            conn.execute("SELECT sql FROM sqlite_master WHERE name = 'evidence_spans'").fetchone()
            == before
        )
        for table in ("embedding_configs", "embedding_jobs", "embedding_vectors"):
            assert conn.execute(f"SELECT count(*) FROM {table}").fetchone() == (0,)
    command.downgrade(config, "4c17f5089ea3")
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT * FROM projects").fetchall() == project
        assert (
            conn.execute("SELECT name FROM sqlite_master WHERE name LIKE 'embedding_%'").fetchall()
            == []
        )
