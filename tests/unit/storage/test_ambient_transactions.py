from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine

from thoth.adapters.storage.transaction import (
    SqliteAtomicUnitOfWork,
    read_connection,
    write_connection,
)


def test_ambient_reads_see_staged_writes_and_other_connections_see_only_commit(
    tmp_path: Path,
) -> None:
    engine = create_engine("sqlite+pysqlite:///" + (tmp_path / "store.db").as_posix())
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE records (value TEXT)")
    try:
        with SqliteAtomicUnitOfWork(engine, immediate=True).transaction():
            with write_connection(engine) as writer:
                writer.exec_driver_sql("INSERT INTO records VALUES ('candidate')")
                with read_connection(engine) as reader:
                    assert reader is writer
                    assert reader.exec_driver_sql("SELECT count(*) FROM records").scalar() == 1
            with engine.connect() as independent:
                assert independent.exec_driver_sql("SELECT count(*) FROM records").scalar() == 0
        with engine.connect() as independent:
            assert independent.exec_driver_sql("SELECT count(*) FROM records").scalar() == 1
    finally:
        engine.dispose()


def test_nested_failure_rolls_back_and_releases_ambient_connection(tmp_path: Path) -> None:
    engine = create_engine("sqlite+pysqlite:///" + (tmp_path / "store.db").as_posix())
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE records (value TEXT)")
    try:
        with (
            pytest.raises(RuntimeError, match="injected"),
            SqliteAtomicUnitOfWork(engine, immediate=True).transaction(),
        ):
            with write_connection(engine) as connection:
                connection.exec_driver_sql("INSERT INTO records VALUES ('first')")
            with SqliteAtomicUnitOfWork(engine).transaction():
                with write_connection(engine) as connection:
                    connection.exec_driver_sql("INSERT INTO records VALUES ('second')")
                raise RuntimeError("injected")
        with read_connection(engine) as connection:
            assert connection.exec_driver_sql("SELECT count(*) FROM records").scalar() == 0
    finally:
        engine.dispose()


def test_cross_engine_read_and_write_cannot_escape_ambient_uow(tmp_path: Path) -> None:
    engine = create_engine("sqlite+pysqlite:///" + (tmp_path / "first.db").as_posix())
    other = create_engine("sqlite+pysqlite:///" + (tmp_path / "other.db").as_posix())
    try:
        with SqliteAtomicUnitOfWork(engine).transaction():
            with pytest.raises(RuntimeError, match="mix SQLite engines"), read_connection(other):
                pass
            with pytest.raises(RuntimeError, match="mix SQLite engines"), write_connection(other):
                pass
    finally:
        engine.dispose()
        other.dispose()
