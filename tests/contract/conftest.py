from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

from thoth.adapters.storage import (
    SqliteArtifactLedger,
    SqliteGovernanceStore,
    SqliteLedger,
    SqliteOperationStore,
    SqliteProjectStore,
    migrate_sqlite_database,
)
from thoth.application.commands import OperationCommandHandlers, ProjectCommandHandlers
from thoth.protocol.bus import CommandBus
from thoth.protocol.registry import MethodRegistry


@dataclass
class FixedClock:
    instant: datetime = datetime(2026, 8, 30, 6, 40, tzinfo=UTC)

    def now(self) -> datetime:
        return self.instant


class SequenceIds:
    def __init__(self) -> None:
        self._next = 0

    def new(self, prefix: str) -> str:
        self._next += 1
        return f"{prefix}:{self._next}"


@pytest.fixture
def command_bus(tmp_path: Path) -> Iterator[CommandBus]:
    migrate_sqlite_database(tmp_path / "thoth.sqlite3")
    ledger = SqliteLedger(tmp_path / "thoth.sqlite3")
    ledger.initialize()
    clock = FixedClock()
    operations = SqliteOperationStore(ledger.engine)
    ids = SequenceIds()
    projects = ProjectCommandHandlers(
        store=SqliteProjectStore(ledger.engine),
        governance=SqliteGovernanceStore(ledger.engine),
        artifacts=SqliteArtifactLedger(ledger.engine),
        ledger=ledger,
        clock=clock,
        ids=ids,
    )
    operation_handlers = OperationCommandHandlers(operations)
    registry = MethodRegistry()
    registry.register("project/create", projects.create)
    registry.register("project/read", projects.read)
    registry.register("operation/read", operation_handlers.read)
    registry.register("operation/result/read", operation_handlers.result)
    yield CommandBus(registry, operations, clock, ids)
    ledger.close()
