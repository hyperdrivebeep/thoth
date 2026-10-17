"""Whole normal-entry clock sequence with observable journal timestamps."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import JsonValue
from pytest import MonkeyPatch
from tests.integration.test_a03_multi_wave_counter_search import (
    test_thread_input_runs_bounded_multi_wave_counter_research as run_scenario,
)

from thoth.adapters.runtime import SystemClock
from thoth.application.services.operation_journal import OperationJournal
from thoth.domain.event import EventRecord
from thoth.domain.operation import OperationRecord


async def observe(scenario: str, tmp_path: Path, monkeypatch: MonkeyPatch) -> list[str]:
    events: list[str] = []
    calls = 0

    def now(self: SystemClock) -> datetime:
        nonlocal calls
        result = datetime(2026, 9, 1, tzinfo=UTC) + timedelta(milliseconds=100 * calls)
        calls += 1
        events.append("clock:" + result.isoformat())
        return result

    original = OperationJournal.append

    def append(
        self: OperationJournal,
        *,
        project_id: str,
        operation_id: str,
        event_type: str,
        payload: dict[str, JsonValue],
    ) -> EventRecord:
        event = original(
            self,
            project_id=project_id,
            operation_id=operation_id,
            event_type=event_type,
            payload=payload,
        )
        events.append("event:" + event.event_type + ":" + event.created_at.isoformat())
        return event

    original_started = OperationJournal.prepare_started

    def prepare_started(self: OperationJournal, operation: OperationRecord) -> EventRecord:
        event = original_started(self, operation)
        events.append("event:" + event.event_type + ":" + event.created_at.isoformat())
        return event

    monkeypatch.setattr(SystemClock, "now", now)
    monkeypatch.setattr(OperationJournal, "append", append)
    monkeypatch.setattr(OperationJournal, "prepare_started", prepare_started)
    expected = {"converged": "ELIMINATED_WITHIN_SCOPE", "budget": "BUDGET_EXHAUSTED"}
    await run_scenario(tmp_path, scenario, expected[scenario])
    return events
