from __future__ import annotations

import asyncio
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from threading import get_ident

from sqlalchemy import Connection, Engine

from thoth.domain.resource_scope import resource_use_transaction
from thoth.ports.runtime import AtomicUnitOfWorkPort


def _task() -> object | None:
    try:
        return asyncio.current_task()
    except RuntimeError:
        return None


@dataclass
class _Transaction:
    engine: Engine
    connection: Connection
    thread: int
    task: object | None
    rollback_only: bool = False
    active: bool = True

    def require_owner(self, engine: Engine) -> None:
        if self.thread != get_ident() or self.task is not _task() or not self.active:
            raise RuntimeError("ambient SQLite transaction belongs to another task/thread or ended")
        if self.engine is not engine:
            raise RuntimeError("cannot mix SQLite engines in one atomic unit of work")


_AMBIENT: ContextVar[_Transaction | None] = ContextVar(
    "thoth_sqlite_ambient_transaction",
    default=None,
)


@contextmanager
def read_connection(engine: Engine) -> Generator[Connection, None, None]:
    ambient = _AMBIENT.get()
    if ambient is not None:
        ambient.require_owner(engine)
        yield ambient.connection
        return
    with engine.connect() as connection:
        yield connection


@contextmanager
def write_connection(engine: Engine) -> Generator[Connection, None, None]:
    ambient = _AMBIENT.get()
    if ambient is not None:
        ambient.require_owner(engine)
        try:
            yield ambient.connection
        except BaseException:
            ambient.rollback_only = True
            raise
        return
    with engine.begin() as connection:
        yield connection


class SqliteAtomicUnitOfWork(AtomicUnitOfWorkPort):
    def __init__(self, engine: Engine, *, immediate: bool = False) -> None:
        self._engine = engine
        self._immediate = immediate

    @contextmanager
    def transaction(self) -> Generator[None, None, None]:
        ambient = _AMBIENT.get()
        if ambient is not None:
            ambient.require_owner(self._engine)
            # Nested scopes join their owner; never upgrade a live transaction.
            try:
                yield
            except BaseException:
                ambient.rollback_only = True
                raise
            return
        with resource_use_transaction(), self._engine.begin() as connection:
            if self._immediate:
                connection.exec_driver_sql("BEGIN IMMEDIATE")
            transaction = _Transaction(self._engine, connection, get_ident(), _task())
            token = _AMBIENT.set(transaction)
            try:
                yield
                if transaction.rollback_only:
                    raise RuntimeError("atomic unit of work is rollback-only")
            finally:
                transaction.active = False
                _AMBIENT.reset(token)
