"""Exercise the two query predicates on migrated SQLite and reject any write during reads."""

import hashlib
import sqlite3
from pathlib import Path
from typing import Any

from sqlalchemy import event, insert
from tests.unit.test_running_research import operation

from thoth.adapters.storage.operations import SqliteOperationStore
from thoth.adapters.storage.schema import dependency_states, working_heads
from thoth.application.services.research_freshness import ResearchFreshnessService
from thoth.application.services.running_research import research_is_running
from thoth.apps.runtime import create_runtime
from thoth.domain.enums import OperationState


def snapshot(database: str) -> str:
    with sqlite3.connect(f"file:{Path(database).as_posix()}?mode=ro", uri=True) as connection:
        return hashlib.sha256("\n".join(connection.iterdump()).encode()).hexdigest()


def test_freshness_and_running_predicates_preserve_all_database_content(tmp_path: Path) -> None:
    runtime = create_runtime(tmp_path)
    engine = runtime.ledger.engine
    store = SqliteOperationStore(engine)
    freshness_cases = (
        ("absent", None, "INVALIDATED", "UNAVAILABLE", False),
        ("current", "a" * 64, None, "CURRENT", True),
        ("changed", "b" * 64, None, "REVIEW_REQUIRED", False),
        ("stale", "a" * 64, "STALE", "REVIEW_REQUIRED", False),
        ("recalculate", "a" * 64, "RECALCULATION_REQUIRED", "REVIEW_REQUIRED", False),
        ("invalid", "b" * 64, "INVALIDATED", "INVALIDATED", False),
    )
    running_cases = (
        ("pending", "thread/steer", OperationState.PENDING, True),
        ("running", "thread/input", OperationState.RUNNING, True),
        ("finished", "thread/start", OperationState.SUCCEEDED, False),
        ("cancelled", "thread/start", OperationState.CANCELLED, False),
        ("unrelated", "memory/edit/propose", OperationState.RUNNING, False),
    )
    try:
        with engine.begin() as connection:
            for name, head, status, _state, _eligible in freshness_cases:
                if head:
                    connection.execute(
                        insert(working_heads).values(
                            project_id="p",
                            aggregate_key=f"ACTION:{name}",
                            revision_digest=head,
                            updated_at="2026-10-12T00:00:00Z",
                        )
                    )
                if status:
                    connection.execute(
                        insert(dependency_states).values(
                            project_id="p",
                            entity_ref=f"ACTION:{name}",
                            status=status,
                            caused_by_revision="c" * 64,
                            updated_at="2026-10-12T00:00:00Z",
                        )
                    )
        for project, method, state, _expected in running_cases:
            store.claim(operation(method, state, project))
        database = engine.url.database
        assert database is not None
        original = snapshot(database)
        statements: list[str] = []

        def only_select(
            _connection: Any,
            _cursor: Any,
            statement: str,
            _parameters: Any,
            _context: Any,
            _executemany: bool,
        ) -> None:
            assert statement.lstrip().upper().startswith("SELECT"), statement
            statements.append(statement)

        event.listen(engine, "before_cursor_execute", only_select)
        try:
            service = ResearchFreshnessService(runtime.ledger)
            for name, _head, _status, state, eligible in freshness_cases:
                result = service.evaluate_entity("p", f"ACTION:{name}", "a" * 64)
                assert result.state == state and result.execution_eligible is eligible
            for project, _method, _state, expected in running_cases:
                assert research_is_running(store, project) is expected
            assert research_is_running(store, "no-operations") is False
        finally:
            event.remove(engine, "before_cursor_execute", only_select)
        assert len(statements) >= 18
        assert snapshot(database) == original
    finally:
        runtime.close()
