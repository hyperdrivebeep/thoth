"""Recursive improvement keeps its phases apart, and each phase commits all of its writes or none.

The phases are separate by design: observing the failure and reserving the evaluation request, the
evaluation itself (no database work), and the finalize step that publishes the run and exposures.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
from tests.atomicity.harness import Snapshot, assert_phase_delta, snapshot
from tests.atomicity.test_improvement import coordinator, projection
from tests.integration.storage_coverage_helpers import prepare_project

from thoth.adapters.improvement import DeterministicIndependentImprovementEvaluator
from thoth.adapters.storage.bundle import SqliteStoreBundle
from thoth.application.services.control_record_service import ControlRecordService
from thoth.application.services.recursive_improvement import RecursiveImprovementCoordinator
from thoth.apps.runtime import AppRuntime
from thoth.domain.control_record import ControlRecord
from thoth.domain.improvement import ImprovementRunState


def failure(attempt: str) -> dict[str, object]:
    value = projection()
    attempts: list[dict[str, str]] = [
        {
            "attempt_id": attempt,
            "failure_class": "SEMANTIC",
            "failure_detail": "controlled failure",
        }
    ]
    value["recovery"] = {"baseline_head_before": "0" * 64, "attempts": attempts}
    return value


async def ready(
    tmp_path: Path,
) -> tuple[AppRuntime, str, RecursiveImprovementCoordinator]:
    """One failure is already observed, so the next distinct failure reaches the threshold."""
    runtime, project = await prepare_project(tmp_path)
    observer = coordinator(SqliteStoreBundle(runtime.ledger, tmp_path))
    first = await observer.observe(
        project_id=project, thread_id="thread:improve", r2_projection=failure("attempt:one")
    )
    assert first is not None and first.state == ImprovementRunState.BELOW_THRESHOLD
    return runtime, project, observer


def records(state: Snapshot) -> Counter[tuple[str, str]]:
    return Counter(
        (row["record_type"], row["state"])
        for row in (json.loads(raw) for raw in state["control_records"])
        if row["namespace"] == "IMPROVEMENT_RUNTIME"
    )


# The observation, the reservation and its FAILED mark are what survives an interrupted evaluation.
RESERVED_THEN_FAILED = Counter(
    {
        ("FAILURE_OBSERVATION", "OBSERVED"): 1,
        ("EVALUATION_REQUEST", "EVALUATING"): 1,
        ("EVALUATION_REQUEST", "FAILED"): 1,
    }
)


def fail_create(patch: pytest.MonkeyPatch, record_type: str) -> list[str]:
    hits: list[str] = []
    original = ControlRecordService.create

    def create(self: ControlRecordService, **kwargs: Any) -> ControlRecord:
        if kwargs.get("record_type") == record_type:
            hits.append(record_type)
            raise RuntimeError("injected fault writing a " + record_type)
        return original(self, **kwargs)

    patch.setattr(ControlRecordService, "create", create)
    return hits


async def observe_second(observer: RecursiveImprovementCoordinator, project: str):
    return await observer.observe(
        project_id=project, thread_id="thread:improve", r2_projection=failure("attempt:two")
    )


async def test_fault_while_reserving_the_evaluation_commits_nothing_and_retry_completes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, project, observer = await ready(tmp_path)
    try:
        before = snapshot(runtime.ledger.engine)
        with monkeypatch.context() as patch:
            hits = fail_create(patch, "EVALUATION_REQUEST")
            with pytest.raises(RuntimeError, match="injected fault writing a EVALUATION_REQUEST"):
                await observe_second(observer, project)
        assert hits == ["EVALUATION_REQUEST"]
        assert_phase_delta(before, snapshot(runtime.ledger.engine))
        done = await observe_second(observer, project)
        assert done is not None and done.state != ImprovementRunState.BELOW_THRESHOLD
        assert done.failure_count == 2
    finally:
        runtime.close()


async def test_fault_during_the_evaluation_leaves_only_the_designed_reservation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, project, observer = await ready(tmp_path)
    try:
        before = snapshot(runtime.ledger.engine)

        def interrupted(*args: object, **kwargs: object) -> None:
            raise RuntimeError("evaluation interrupted")

        with monkeypatch.context() as patch:
            patch.setattr(DeterministicIndependentImprovementEvaluator, "evaluate", interrupted)
            with pytest.raises(RuntimeError, match="evaluation interrupted"):
                await observe_second(observer, project)
        after = snapshot(runtime.ledger.engine)
        # By design the observation and the reservation are committed before the evaluation starts,
        # and the reservation is then marked FAILED. No run, exposure or active pointer is written.
        assert records(after) - records(before) == RESERVED_THEN_FAILED
        again = await observe_second(observer, project)
        assert again is not None and again.state == ImprovementRunState.HELD
        assert again.rollback_reason == "FAILURE_EVENT_ALREADY_OBSERVED"
    finally:
        runtime.close()


@pytest.mark.parametrize("record_type", ["EXPOSURE", "RUN"])
async def test_fault_while_finalizing_publishes_no_run_and_no_exposure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, record_type: str
) -> None:
    runtime, project, observer = await ready(tmp_path)
    try:
        before = snapshot(runtime.ledger.engine)
        with monkeypatch.context() as patch:
            hits = fail_create(patch, record_type)
            with pytest.raises(RuntimeError, match="injected fault writing a " + record_type):
                await observe_second(observer, project)
        if record_type == "EXPOSURE":
            assert hits == ["EXPOSURE"]
        after = snapshot(runtime.ledger.engine)
        # The finalize step is one transaction: no run and no exposure survive it. The earlier phase
        # does, and the reservation is marked FAILED because the evaluation did not complete.
        assert records(after) - records(before) == RESERVED_THEN_FAILED
        again = await observe_second(observer, project)
        assert again is not None and again.state == ImprovementRunState.HELD
        assert again.rollback_reason == "FAILURE_EVENT_ALREADY_OBSERVED"
    finally:
        runtime.close()
