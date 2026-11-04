"""thread/start creates the thread, its decision object and its investigation as one unit."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.atomicity.fault_helpers import expect_rolled_back_and_retry
from tests.integration.scoped_runtime import create_runtime
from tests.integration.storage_coverage_helpers import request, value

from thoth.adapters.storage.decision_object import SqliteDecisionObjectStore
from thoth.adapters.storage.threads import SqliteThreadStore
from thoth.application.services.investigation_service import InvestigationService

PROJECT = "project:thread-start-faults"

POINTS: list[tuple[type[object], str]] = [
    (SqliteDecisionObjectStore, "add_object"),
    (SqliteDecisionObjectStore, "append_audit"),
    (SqliteThreadStore, "update"),
    (InvestigationService, "start"),
]


@pytest.mark.parametrize("store,point", POINTS)
async def test_thread_start_fault_after_a_partial_write_leaves_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, store: type[object], point: str
) -> None:
    runtime = create_runtime(tmp_path / "workspace")
    try:
        created: dict[str, object] = {
            "project_id": PROJECT,
            "name": "Thread start",
            "cutoff_at": "2026-09-01T00:00:00Z",
        }
        value(await runtime.bus.dispatch(request("project/create", "project", created)))
        body: dict[str, object] = {
            "project_id": PROJECT,
            "thread_id": "thread:thread-start-faults",
            "problem": "Why did detection fall in rain?",
            "scope": {"workstream": "integration"},
        }
        retried = await expect_rolled_back_and_retry(
            runtime, monkeypatch, store, point, "thread/start", body
        )
        assert retried["thread_id"] == "thread:thread-start-faults"
        assert retried["current_object_ids"]
    finally:
        runtime.close()
