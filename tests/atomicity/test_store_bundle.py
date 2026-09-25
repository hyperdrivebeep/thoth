from __future__ import annotations

import asyncio
from contextvars import copy_context
from pathlib import Path

import pytest
from sqlalchemy import Engine, create_engine

from thoth.adapters.storage.transaction import SqliteAtomicUnitOfWork, write_connection
from thoth.domain.resource_scope import (
    current_resource_uses,
    record_resource_use,
    resource_use_scope,
)


def database(path: Path) -> Engine:
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE sample (id INTEGER PRIMARY KEY)")
    return engine


def test_swallowed_nested_failure_cannot_commit_partial_checkpoint(tmp_path: Path) -> None:
    engine = database(tmp_path / "nested.sqlite3")
    uow = SqliteAtomicUnitOfWork(engine)
    try:
        with pytest.raises(RuntimeError, match="rollback-only"), uow.transaction():
            with write_connection(engine) as connection:
                connection.exec_driver_sql("INSERT INTO sample VALUES (1)")
            try:
                with uow.transaction():
                    with write_connection(engine) as connection:
                        connection.exec_driver_sql("INSERT INTO sample VALUES (2)")
                    raise ValueError("nested failure")
            except ValueError:
                pass
        with engine.connect() as connection:
            assert connection.exec_driver_sql("SELECT count(*) FROM sample").scalar_one() == 0
    finally:
        engine.dispose()


async def test_child_task_cannot_use_parent_ambient_connection(tmp_path: Path) -> None:
    engine = database(tmp_path / "task.sqlite3")
    uow = SqliteAtomicUnitOfWork(engine)

    async def child() -> None:
        with write_connection(engine) as connection:
            connection.exec_driver_sql("INSERT INTO sample VALUES (2)")

    try:
        with uow.transaction():
            with write_connection(engine) as connection:
                connection.exec_driver_sql("INSERT INTO sample VALUES (1)")
            with pytest.raises(RuntimeError, match="task"):
                await asyncio.create_task(child())
        with engine.connect() as connection:
            assert connection.exec_driver_sql("SELECT id FROM sample").scalars().all() == [1]
    finally:
        engine.dispose()


def test_mixed_engine_rejected_before_write(tmp_path: Path) -> None:
    first, second = database(tmp_path / "first.sqlite3"), database(tmp_path / "second.sqlite3")
    try:
        with (
            SqliteAtomicUnitOfWork(first).transaction(),
            pytest.raises(RuntimeError, match="mix SQLite engines"),
            write_connection(second),
        ):
            pytest.fail("foreign connection exposed")
        with second.connect() as connection:
            assert connection.exec_driver_sql("SELECT count(*) FROM sample").scalar_one() == 0
    finally:
        first.dispose()
        second.dispose()


def test_expired_same_task_context_cannot_reuse_committed_connection(tmp_path: Path) -> None:
    engine = database(tmp_path / "expired.sqlite3")
    try:
        with SqliteAtomicUnitOfWork(engine).transaction():
            saved = copy_context()

        def late_write() -> None:
            with write_connection(engine) as connection:
                connection.exec_driver_sql("INSERT INTO sample VALUES (1)")

        with pytest.raises(RuntimeError, match="ended"):
            saved.run(late_write)
        with engine.connect() as connection:
            assert connection.exec_driver_sql("SELECT count(*) FROM sample").scalar_one() == 0
    finally:
        engine.dispose()


def test_caught_write_failure_rolls_back_rows_and_staged_resource_uses(tmp_path: Path) -> None:
    engine = database(tmp_path / "resource.sqlite3")
    try:
        with resource_use_scope("project:atomicity"):
            record_resource_use("project:atomicity", "artifact:before", "READ")
            before = current_resource_uses()
            with (
                pytest.raises(RuntimeError, match="rollback-only"),
                SqliteAtomicUnitOfWork(engine).transaction(),
            ):
                try:
                    with write_connection(engine) as connection:
                        connection.exec_driver_sql("INSERT INTO sample VALUES (1)")
                        record_resource_use("project:atomicity", "artifact:staged", "WRITE")
                        raise ValueError("write preparation failed")
                except ValueError:
                    pass
            assert current_resource_uses() == before
        with engine.connect() as connection:
            assert connection.exec_driver_sql("SELECT count(*) FROM sample").scalar_one() == 0
    finally:
        engine.dispose()


async def test_inherited_context_in_worker_thread_is_rejected(tmp_path: Path) -> None:
    engine = database(tmp_path / "worker.sqlite3")

    def worker() -> None:
        with write_connection(engine):
            pytest.fail("thread received parent connection")

    try:
        with (
            SqliteAtomicUnitOfWork(engine).transaction(),
            pytest.raises(RuntimeError, match="thread"),
        ):
            await asyncio.to_thread(worker)
    finally:
        engine.dispose()
