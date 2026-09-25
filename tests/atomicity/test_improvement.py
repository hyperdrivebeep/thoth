from pathlib import Path
from typing import Any

import pytest
from tests.atomicity.harness import assert_phase_delta, snapshot
from tests.integration.storage_coverage_helpers import prepare_project

from thoth.adapters.improvement import DeterministicIndependentImprovementEvaluator
from thoth.adapters.runtime import SystemClock, UuidIdGenerator
from thoth.adapters.storage.bundle import SqliteStoreBundle
from thoth.application.services import BaselineRouter, BaselineService
from thoth.application.services.control_record_service import ControlRecordService
from thoth.application.services.improvement_evaluation_gate import ImprovementEvaluationGate
from thoth.application.services.recursive_improvement import RecursiveImprovementCoordinator
from thoth.domain.control_record import ControlRecord
from thoth.domain.improvement import ImprovementRunState, RecursiveImprovementResult


def coordinator(stores: SqliteStoreBundle) -> RecursiveImprovementCoordinator:
    clock, ids = SystemClock(), UuidIdGenerator()
    controls = ControlRecordService(store=stores.controls, clock=clock, ids=ids)
    baselines = BaselineService(
        store=stores.baselines, ledger=stores.ledger, router=BaselineRouter(), clock=clock, ids=ids
    )
    gate = ImprovementEvaluationGate(
        ledger=stores.ledger,
        projects=stores.projects,
        governance=stores.governance,
        behaviors=stores.behaviors,
        baselines=baselines,
        records=stores.controls,
        controls=controls,
        sessions=stores.auth,
        clock=clock,
    )
    return RecursiveImprovementCoordinator(
        records=stores.controls,
        controls=controls,
        evaluator=DeterministicIndependentImprovementEvaluator(),
        unit_of_work=stores.atomic_uow,
        ledger=stores.ledger,
        evaluation_gate=gate,
        clock=clock,
        ids=ids,
    )


def projection() -> dict[str, object]:
    return {
        "terminal_state": "HOLD",
        "action_id": "action:local-fixture",
        "recovery": {
            "baseline_head_before": "0" * 64,
            "attempts": [
                {
                    "attempt_id": "attempt:observed",
                    "failure_class": "SEMANTIC",
                    "failure_detail": "controlled failure",
                }
            ],
        },
    }


async def test_below_threshold_counter_and_summary_rollback_together(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, project = await prepare_project(tmp_path)
    stores = SqliteStoreBundle(runtime.ledger, tmp_path)
    observer = coordinator(stores)
    before = snapshot(runtime.ledger.engine)
    original = ControlRecordService.create

    def fail(service: ControlRecordService, **kwargs: Any) -> ControlRecord:
        if kwargs.get("record_type") == "OBSERVATION_RESULT":
            raise RuntimeError("observation summary failure")
        return original(service, **kwargs)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(ControlRecordService, "create", fail)
            with pytest.raises(RuntimeError, match="observation summary failure"):
                await observer.observe(
                    project_id=project, thread_id="thread:counter", r2_projection=projection()
                )
        assert_phase_delta(before, snapshot(runtime.ledger.engine))
        result = await observer.observe(
            project_id=project, thread_id="thread:counter", r2_projection=projection()
        )
        assert result is not None and result.state == ImprovementRunState.BELOW_THRESHOLD
        assert result.failure_count == 1
    finally:
        runtime.close()


async def test_missing_summary_recovers_exact_committed_run_without_reobserving(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, project = await prepare_project(tmp_path)
    stores = SqliteStoreBundle(runtime.ledger, tmp_path)
    observer = coordinator(stores)
    try:
        event = observer._failure_event(project, "thread:counter", projection())  # pyright: ignore[reportPrivateUsage]
        assert event is not None
        result = observer._result(  # pyright: ignore[reportPrivateUsage]
            project_id=project,
            thread_id="thread:counter",
            state=ImprovementRunState.EVALUATED,
            fingerprint="a" * 64,
            count=2,
            baseline="b" * 64,
            active="b" * 64,
            now=SystemClock().now(),
        )
        controls = ControlRecordService(
            store=stores.controls, clock=SystemClock(), ids=UuidIdGenerator()
        )
        with stores.atomic_uow.transaction():
            controls.create(
                project_id=project,
                namespace="IMPROVEMENT_RUNTIME",
                record_type="FAILURE_OBSERVATION",
                record_id=f"failure:{event}",
                state="OBSERVED",
                payload={
                    "thread_id": "thread:counter",
                    "failure_fingerprint": result.failure_fingerprint,
                    "ordinal": 2,
                },
            )
            observer._persist_result(result)  # pyright: ignore[reportPrivateUsage]

        async def no_reobserve(**kwargs: Any) -> RecursiveImprovementResult:
            raise AssertionError("Use the already committed run; do not start observation again")

        monkeypatch.setattr(observer, "_observe", no_reobserve)
        recovered = await observer.observe(
            project_id=project, thread_id="thread:counter", r2_projection=projection()
        )
        assert recovered == result
        saved = stores.controls.read(project, "IMPROVEMENT_RUNTIME", f"observation-result:{event}")
        assert saved is not None and saved.payload["result"] == result.model_dump(mode="json")
    finally:
        runtime.close()
