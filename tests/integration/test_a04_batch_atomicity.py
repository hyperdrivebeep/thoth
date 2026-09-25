from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from tests.integration.storage_coverage_helpers import (
    domain_snapshot,
    prepare_project,
    request,
    value,
)

from thoth.adapters.runtime import SystemClock
from thoth.adapters.storage.action import SqliteActionStore
from thoth.apps.runtime import create_runtime
from thoth.domain.action_full import ActionRecord
from thoth.domain.canonical import canonical_payload, domain_digest


async def test_action_generate_failure_rolls_back_entire_batch_after_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    runtime, project = await prepare_project(workspace)
    try:
        thread = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "thread",
                    {
                        "project_id": project,
                        "problem": "Compare bounded alternatives",
                    },
                )
            )
        )
        before = domain_snapshot(runtime.ledger.engine)
        original = SqliteActionStore.add_action
        calls = 0

        def fail_second(store: SqliteActionStore, record: ActionRecord) -> None:
            nonlocal calls
            calls += 1
            original(store, record)
            if calls == 2:
                raise RuntimeError("injected second alternative failure")

        with monkeypatch.context() as patch:
            patch.setattr(SqliteActionStore, "add_action", fail_second)
            failed = await runtime.bus.dispatch(
                request(
                    "action/generate",
                    "generate",
                    {
                        "project_id": project,
                        "object_id": thread["current_object_ids"][0],
                        "decision_need": "Compare bounded alternatives",
                        "evidence_scope": [],
                    },
                )
            )
            assert calls == 2 and failed.error is not None
        assert domain_snapshot(runtime.ledger.engine) == before
    finally:
        runtime.close()
    reopened = create_runtime(workspace)
    try:
        assert domain_snapshot(reopened.ledger.engine) == before
    finally:
        reopened.close()


async def test_action_audit_digest_binds_the_timestamp_that_is_actually_stored(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ticks = 0

    def advancing_now(clock: SystemClock) -> datetime:
        nonlocal ticks
        del clock
        ticks += 1
        return datetime(2026, 9, 5, tzinfo=UTC) + timedelta(seconds=ticks)

    monkeypatch.setattr(SystemClock, "now", advancing_now)
    runtime, project = await prepare_project(tmp_path / "workspace")
    try:
        thread = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "thread",
                    {
                        "project_id": project,
                        "problem": "Compare bounded alternatives",
                    },
                )
            )
        )
        value(
            await runtime.bus.dispatch(
                request(
                    "action/generate",
                    "generate",
                    {
                        "project_id": project,
                        "object_id": thread["current_object_ids"][0],
                        "decision_need": "Compare bounded alternatives",
                        "evidence_scope": [],
                    },
                )
            )
        )
        records = SqliteActionStore(runtime.ledger.engine).list_audit(project, None)
        assert records
        for record in records:
            payload = record.model_dump(
                mode="python", exclude={"audit_id", "event_digest", "schema_version"}
            )
            assert record.event_digest == domain_digest(
                "ACTION_AUDIT", "1.0.0", canonical_payload(payload)
            )
    finally:
        runtime.close()
