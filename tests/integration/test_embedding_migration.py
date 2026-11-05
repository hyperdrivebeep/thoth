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


def test_truncation_columns_are_added_and_removed_without_touching_stored_vectors(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    db = tmp_path / "vectors.sqlite3"
    monkeypatch.setenv("THOTH_ALEMBIC_URL", "sqlite+pysqlite:///" + db.as_posix())
    config = Config("alembic.ini")
    command.upgrade(config, "8e3b5f1a7c42")
    row = ("project:p", "k" * 64, "s" * 64, b"\x01\x02\x03")
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO embedding_vectors (project_id, content_key, vector_sha256, data)"
            " VALUES (?,?,?,?)",
            row,
        )
        columns = [r[1] for r in conn.execute("PRAGMA table_info(embedding_vectors)")]
    assert columns == ["project_id", "content_key", "vector_sha256", "data"]

    command.upgrade(config, "head")
    with sqlite3.connect(db) as conn:
        columns = [r[1] for r in conn.execute("PRAGMA table_info(embedding_vectors)")]
        assert columns == [*columns[:4], "truncated", "source_tokens"]
        # Rows made before the columns existed keep every old value and read as "not recorded".
        assert conn.execute("SELECT * FROM embedding_vectors").fetchall() == [(*row, None, None)]
        conn.execute(
            "INSERT INTO embedding_vectors VALUES ('project:p', ?, ?, ?, 1, 777)",
            ("j" * 64, "t" * 64, b"\x04"),
        )

    command.downgrade(config, "8e3b5f1a7c42")
    with sqlite3.connect(db) as conn:
        columns = [r[1] for r in conn.execute("PRAGMA table_info(embedding_vectors)")]
        assert columns == ["project_id", "content_key", "vector_sha256", "data"]
        assert conn.execute(
            "SELECT project_id, content_key, vector_sha256, data FROM embedding_vectors"
            " ORDER BY content_key"
        ).fetchall() == [("project:p", "j" * 64, "t" * 64, b"\x04"), row]

    command.upgrade(config, "head")
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT count(*) FROM embedding_vectors").fetchone() == (2,)
