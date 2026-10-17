"""101-V: coordinator publication, interruption and committed-run replay boundaries.

Selected pair completion and coordinator publication are distinct UoWs. Legacy finalization
failure is followed by a separate FAILED request marker. Public caller cleanup is audited
separately, not excluded by dropping control_records. Models/sandbox are scripted; selected
comparisons are real bounded local programs. No product source or fixture is modified.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from tests.integration.storage_coverage_helpers import domain_snapshot, request, value
from tests.integration.test_a05_bounded_recovery import RecoverySandboxAdapter, prepare_recovery
from tests.integration.test_m5_improvement_handlers_atomicity import Snapshot, check_reopened

from thoth.adapters.evaluators.catalog import FrozenEvaluationCatalog
from thoth.adapters.improvement import DeterministicIndependentImprovementEvaluator
from thoth.adapters.storage.behavior_artifact import SqliteBehaviorArtifactStore
from thoth.adapters.storage.control_record import SqliteControlRecordStore
from thoth.adapters.storage.evaluation_run import SqliteEvaluationRunStore
from thoth.application.services.evaluation_producer import EvaluationProducer, SelectedEvaluation
from thoth.application.services.improvement_observation_results import ImprovementObservationResults
from thoth.application.services.paired_evaluation_service import PairedEvaluationService
from thoth.application.services.recursive_improvement import (
    PreparedImprovement,
    PreparedPair,
    RecursiveImprovementCoordinator,
)
from thoth.apps.runtime import AppRuntime
from thoth.domain.behavior_artifact import BehaviorArtifactKind
from thoth.domain.behavior_execution import BehaviorExecutionRecord
from thoth.domain.canonical import head_set_digest
from thoth.domain.control_record import ControlRecord
from thoth.domain.evaluation_run import (
    EvaluationBinding,
    EvaluationCasePack,
    EvaluationScorer,
    PairedRunRecord,
    sealed_payload,
)
from thoth.domain.improvement import ImprovementEvaluation, RecursiveImprovementResult
from thoth.domain.research_execution import research_work
from thoth.domain.sandbox import SandboxExecutionState
from thoth.protocol.jsonrpc import JsonRpcResponse

OUT = Path(__file__).resolve().parents[2] / "outputs/refactor-step101-20261015"
FAULT = "101-V injected storage failure"


class CountingEvaluator(DeterministicIndependentImprovementEvaluator):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def evaluate(self, **kwargs: Any) -> ImprovementEvaluation:
        self.calls += 1
        return super().evaluate(**kwargs)


def catalog(project: str) -> FrozenEvaluationCatalog:
    pack = EvaluationCasePack.model_validate(
        sealed_payload(
            "EVALUATION_CASE_PACK",
            "pack_digest",
            {
                "project_id": project,
                "pack_id": "repair-contract",
                "version": "1",
                "cases": [
                    {
                        "public": {"case_id": "needs-repair", "payload": {"needs_repair": True}},
                        "expected_output": {"decision": "REPAIR"},
                        "axis": "quality",
                    }
                ],
                "initial_memory": {},
                "resource_refs": (),
                "expected_visibility": "SCORER_ONLY",
                "expected_values_exposed": False,
                "max_pair_runs": 2,
            },
        )
    )
    scorer = EvaluationScorer.model_validate(
        sealed_payload(
            "EVALUATION_SCORER",
            "scorer_digest",
            {
                "scorer_id": "repair-scorer",
                "version": "1",
                "algorithm": "EXACT_JSON_V1",
            },
        )
    )
    binding = EvaluationBinding.model_validate(
        sealed_payload(
            "EVALUATION_BINDING",
            "binding_digest",
            {
                "project_id": project,
                "binding_id": "repair-binding",
                "component": "WORKFLOW_DEFINITION",
                "executor_id": "BEHAVIOR_COMPONENT_SUBPROCESS_V1",
                "case_pack": pack,
                "scorer": scorer,
                "max_requests": 4,
                "timeout_seconds": 30,
                "max_output_bytes": 262144,
            },
        )
    )
    return FrozenEvaluationCatalog((binding,))


@dataclass
class Harness:
    runtime: AppRuntime
    project: str
    thread: str
    sandbox: RecoverySandboxAdapter
    evaluator: CountingEvaluator

    def snapshot(self) -> Snapshot:
        return domain_snapshot(self.runtime.ledger.engine)

    def records(self, namespace: str, record_type: str) -> tuple[ControlRecord, ...]:
        return SqliteControlRecordStore(self.runtime.ledger.engine).list(
            self.project, namespace, record_type
        )

    async def input(self, key: str) -> JsonRpcResponse:
        return await self.runtime.bus.dispatch(
            request(
                "thread/input",
                key,
                {
                    "project_id": self.project,
                    "thread_id": self.thread,
                },
            )
        )


@asynccontextmanager
async def harness(path: Path, *, selected: bool = True) -> AsyncGenerator[Harness]:
    scenario = "v101-selected" if selected else "v101-legacy"
    project = "project:a05:" + scenario
    sandbox = RecoverySandboxAdapter(
        tuple((SandboxExecutionState.FAILED, "SEMANTIC v101 repeated failure") for _ in range(6))
    )
    evaluator = CountingEvaluator()
    runtime, actual, thread = await prepare_recovery(
        path,
        scenario,
        sandbox,
        max_retries=0,
        improvement_evaluator=evaluator,
        evaluation_catalog=catalog(project) if selected else None,
    )
    h = Harness(runtime, actual, thread, sandbox, evaluator)
    try:
        first = value(await h.input("first"))
        assert first["recursive_improvement"]["state"] == "BELOW_THRESHOLD"
        assert evaluator.calls == 0
        yield h
    finally:
        runtime.close()


def control_rows(snapshot: Snapshot) -> dict[str, ControlRecord]:
    rows = [
        ControlRecord.model_validate_json(json.loads(s)["content_json"])
        for s in snapshot["control_records"]
    ]
    return {r.control_revision_id: r for r in rows}


def added_controls(before: Snapshot, after: Snapshot) -> list[ControlRecord]:
    old, new = control_rows(before), control_rows(after)
    assert old.keys() <= new.keys()
    for key in old:
        assert old[key].model_dump(mode="json") == new[key].model_dump(mode="json")
    return [new[key] for key in new.keys() - old.keys()]


def save(name: str, before: Snapshot, after: Snapshot, **details: Any) -> None:
    def sha(data: Snapshot) -> str:
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"v101-{name}.json").write_text(
        json.dumps(
            {
                "before_sha256": sha(before),
                "after_sha256": sha(after),
                "changed_tables": [t for t in before if before[t] != after[t]],
                "new_controls": [
                    {
                        "namespace": r.namespace,
                        "record_type": r.record_type,
                        "record_id": r.record_id,
                        "state": r.state,
                        "version": r.version,
                    }
                    for r in added_controls(before, after)
                ],
                **details,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )


def unchanged(h: Harness, before: Snapshot, name: str, **details: Any) -> None:
    after = h.snapshot()
    save(name, before, after, **details)
    assert after == before, "HOLD_DEFECT: coordinator UoW left partial publication"
    check_reopened(h.runtime.ledger.engine, before)


def caller_cleanup(
    h: Harness,
    boundary: Snapshot,
    *,
    cancelled: bool = False,
    failed_request: ControlRecord | None = None,
) -> None:
    """Validate the specific _finish_execution and BehaviorThreadEntry finally writes."""
    after = h.snapshot()
    save(
        "caller-" + ("legacy" if failed_request else "cancel" if cancelled else "selected"),
        boundary,
        after,
    )
    for table in boundary:
        if table not in {"control_records", "threads"}:
            assert boundary[table] == after[table], table
    old_threads = {json.loads(r)["thread_id"]: json.loads(r) for r in boundary["threads"]}
    new_threads = {json.loads(r)["thread_id"]: json.loads(r) for r in after["threads"]}
    assert old_threads.keys() == new_threads.keys()
    for identifier, old in old_threads.items():
        new = new_threads[identifier]
        if identifier != h.thread:
            assert old == new
            continue
        assert old["execution_state"] == "RUNNING" and new["execution_state"] == "IDLE"
        assert new["revision"] == old["revision"] + 1
        assert new["working_head_digest"] == head_set_digest(h.runtime.ledger.read_heads(h.project))
        assert datetime.fromisoformat(new["updated_at"]) >= datetime.fromisoformat(
            old["updated_at"]
        )
        keys = {"execution_state", "revision", "working_head_digest", "updated_at"}
        assert {k: v for k, v in old.items() if k not in keys} == {
            k: v for k, v in new.items() if k not in keys
        }
    added = added_controls(boundary, after)
    assert len(added) == (2 if failed_request else 1)
    for row in added:
        if row.namespace == "BEHAVIOR_RUNTIME":
            assert row.record_type == "EXECUTION"
            assert row.state == ("CANCELLED" if cancelled else "HELD")
            prior = [
                r
                for r in control_rows(boundary).values()
                if r.namespace == row.namespace and r.record_id == row.record_id
            ]
            previous = max(prior, key=lambda r: r.version)
            assert previous.state == "RUNNING" and row.version == previous.version + 1
            assert row.supersedes_digest == previous.record_digest
            body = BehaviorExecutionRecord.model_validate(row.payload)
            assert body.thread_id == h.thread and body.project_id == h.project
            assert body.reason_code == (
                "BEHAVIOR_EXECUTION_CANCELLED" if cancelled else "BEHAVIOR_EXECUTION_HELD"
            )
            assert row.payload["snapshots"] == previous.payload["snapshots"]
        else:
            assert failed_request is not None
            assert (row.namespace, row.record_type, row.record_id, row.state, row.version) == (
                "IMPROVEMENT_RUNTIME",
                "EVALUATION_REQUEST",
                failed_request.record_id,
                "FAILED",
                failed_request.version + 1,
            )
            assert row.payload == failed_request.model_dump(mode="json")["payload"]
    check_reopened(h.runtime.ledger.engine, after)


def append_fault(
    patch: pytest.MonkeyPatch, h: Harness, target: Callable[[ControlRecord], bool]
) -> list[ControlRecord]:
    original = SqliteControlRecordStore.append
    seen: list[ControlRecord] = []

    def append(store: SqliteControlRecordStore, record: ControlRecord) -> None:
        original(store, record)
        if record.project_id == h.project:
            seen.append(record)
            if target(record):
                raise RuntimeError(FAULT)

    patch.setattr(SqliteControlRecordStore, "append", append)
    return seen


async def test_selected_complete_pair_survives_coordinator_run_publication_fault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with harness(tmp_path) as h:
        seen: dict[str, Any] = {}
        original_run = EvaluationProducer.run
        original_finish = RecursiveImprovementCoordinator._evaluate_selected  # pyright: ignore[reportPrivateUsage]

        async def run(
            producer: EvaluationProducer, selected: SelectedEvaluation
        ) -> PairedRunRecord:
            result = await original_run(producer, selected)
            assert result.state == "COMPLETE" and result.result is not None
            seen.update(pair=result, before=h.snapshot(), write_start=len(writes))
            return result

        async def finish(
            owner: RecursiveImprovementCoordinator, prepared: PreparedPair
        ) -> RecursiveImprovementResult:
            assert isinstance(prepared.selected.plan.payload.get("automatic_trigger_key"), str)
            seen["plan"] = prepared.selected.plan
            try:
                return await original_finish(owner, prepared)
            except RuntimeError as exc:
                assert str(exc) == FAULT and "before" in seen
                tail = writes[seen["write_start"] :]
                seen["phase_order"] = [(r.namespace, r.record_type, r.state) for r in tail]
                seen["preparation_order"] = [
                    (r.namespace, r.record_type, r.state) for r in writes[: seen["write_start"]]
                ]
                assert [(r.namespace, r.record_type, r.state) for r in tail] == [
                    ("IMPROVEMENT", "EVALUATION_PLAN", "EVALUATED"),
                    ("IMPROVEMENT_RUNTIME", "RUN", "EVALUATED"),
                ]
                assert tail[0].record_id == prepared.selected.plan.record_id
                assert tail[1].record_id == tail[1].payload["run_id"]
                assert tail[1].payload["paired_evaluation_id"] == seen["pair"].spec.pair_id
                unchanged(
                    h, seen["before"], "selected-run-fault", writes=2, complete_pair_preserved=True
                )
                seen["checked"] = True
                raise

        with monkeypatch.context() as patch:
            patch.setattr(EvaluationProducer, "run", run)
            patch.setattr(RecursiveImprovementCoordinator, "_evaluate_selected", finish)
            writes = append_fault(
                patch,
                h,
                lambda r: (
                    r.namespace == "IMPROVEMENT_RUNTIME"
                    and r.record_type == "RUN"
                    and r.payload.get("paired_evaluation_id")
                    == (seen["pair"].spec.pair_id if "pair" in seen else None)
                ),
            )
            failed = await h.input("second")
        assert failed.error is not None and seen.get("checked"), {
            k: seen.get(k) for k in ("phase_order", "preparation_order")
        }
        pair = SqliteEvaluationRunStore(h.runtime.ledger.engine).read(
            h.project, seen["pair"].spec.pair_id
        )
        assert pair == seen["pair"]
        assert h.records("IMPROVEMENT_RUNTIME", "RUN") == ()
        caller_cleanup(h, seen["before"])


async def test_legacy_final_run_fault_and_available_reproposal_paths_are_observed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with harness(tmp_path, selected=False) as h:
        seen: dict[str, Any] = {}
        original = RecursiveImprovementCoordinator._finalize  # pyright: ignore[reportPrivateUsage]

        def finalize(
            owner: RecursiveImprovementCoordinator,
            prepared: PreparedImprovement,
            evaluation: ImprovementEvaluation,
        ) -> RecursiveImprovementResult:
            seen.update(
                before=h.snapshot(), request=prepared.request_record, write_start=len(writes)
            )
            try:
                return original(owner, prepared, evaluation)
            except RuntimeError as exc:
                assert str(exc) == FAULT
                writes_before_failure = writes[seen["write_start"] :]
                seen["phase_order"] = [
                    (r.namespace, r.record_type, r.state) for r in writes_before_failure
                ]
                assert [(r.record_type, r.state) for r in writes_before_failure] == [
                    ("EXPOSURE", stage) for stage in ("OFFLINE", "SANDBOX", "SHADOW", "CANARY")
                ] + [("EVALUATION_REQUEST", "FINALIZED"), ("RUN", "PROMOTED_LOCAL")]
                unchanged(
                    h,
                    seen["before"],
                    "legacy-final-run-fault",
                    final_request_reached=True,
                    registry_rolled_back=True,
                )
                seen["checked"] = True
                raise

        with monkeypatch.context() as patch:
            patch.setattr(RecursiveImprovementCoordinator, "_finalize", finalize)
            writes = append_fault(
                patch, h, lambda r: r.namespace == "IMPROVEMENT_RUNTIME" and r.record_type == "RUN"
            )
            failed = await h.input("second")
        assert failed.error is not None and seen.get("checked") and h.evaluator.calls == 1, (
            seen.get("phase_order")
        )
        caller_cleanup(h, seen["before"], failed_request=seen["request"])
        closed = h.records("IMPROVEMENT_RUNTIME", "EVALUATION_REQUEST")
        assert len(closed) == 1 and closed[0].state == "FAILED"
        failed_state = h.snapshot()
        replay = await h.input("second")
        assert (
            replay.error == failed.error
            and h.evaluator.calls == 1
            and len(h.sandbox.seen_specs) == 2
        )
        assert h.snapshot() == failed_state
        fresh = value(await h.input("third"))["recursive_improvement"]
        assert fresh["state"] == "REJECTED_SAME_DIGEST" and h.evaluator.calls == 1
        artifact = next(
            a
            for a in SqliteBehaviorArtifactStore(h.runtime.ledger.engine).list(
                h.project, BehaviorArtifactKind.WORKFLOW_DEFINITION
            )
            if a.content_digest == closed[0].payload["candidate_digest"]
        )
        proposal = value(
            await h.runtime.bus.dispatch(
                request(
                    "improvement/propose",
                    "new-proposal",
                    {
                        "project_id": h.project,
                        "thread_id": h.thread,
                        "target_component": "WORKFLOW_GATE_ORDER",
                        "scope_key": "legacy-infra-observation",
                        "trigger_refs": [],
                        "baseline_digest": closed[0].payload["baseline_digest"],
                        "candidate_content": artifact.model_dump(mode="json")["content"],
                        "improvement_hypothesis": (
                            "Observe whether a new public revision unlocks legacy retry"
                        ),
                        "evaluation_contract_ref": "legacy-observation-only",
                    },
                )
            )
        )["improvement"]
        revised = value(
            await h.runtime.bus.dispatch(
                request(
                    "improvement/revise",
                    "new-revision",
                    {
                        "project_id": h.project,
                        "improvement_revision_id": proposal["record_id"],
                        "expected_revision_digest": proposal["record_digest"],
                        "patch": {"improvement_hypothesis": "Same content, new metadata revision"},
                        "evidence_refs": [],
                        "reason": "observation only",
                    },
                )
            )
        )["improvement"]
        assert revised["version"] == 2
        assert (
            revised["payload"]["candidate_content"] == artifact.model_dump(mode="json")["content"]
        )
        after_revision = value(await h.input("fourth"))["recursive_improvement"]
        assert after_revision["state"] == "REJECTED_SAME_DIGEST" and h.evaluator.calls == 1
        assert after_revision["candidate_digest"] == closed[0].payload["candidate_digest"]
        assert h.records("IMPROVEMENT_RUNTIME", "EVALUATION_REQUEST") == closed
        check_reopened(h.runtime.ledger.engine, h.snapshot())
        save(
            "legacy-retry-observation",
            failed_state,
            h.snapshot(),
            open_item="RECURSIVE-LEGACY-INFRA-FAILURE-MARKS-FAILED",
            same_key="same error; no sandbox/evaluator invocation",
            fresh_key_state=fresh["state"],
            new_revision_state=revised["state"],
            public_revision_version=revised["version"],
            after_revision_state=after_revision["state"],
            evaluator_calls=h.evaluator.calls,
            legacy_candidate_digest=closed[0].payload["candidate_digest"],
            public_candidate_digest=revised["payload"]["candidate_digest"],
            scope="Configured legacy route only; public records use a different digest namespace.",
        )


@pytest.mark.parametrize("cancel", [True, False])
async def test_producer_interruption_records_reason_and_reraises_before_runner_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cancel: bool
) -> None:
    async with harness(tmp_path) as h:
        seen: dict[str, Any] = {}
        entered = asyncio.Event()
        original_finish = RecursiveImprovementCoordinator._evaluate_selected  # pyright: ignore[reportPrivateUsage]
        original_pair = PairedEvaluationService.run
        runner_calls: list[str] = []

        async def pair_run(
            service: PairedEvaluationService, *args: Any, **kwargs: Any
        ) -> PairedRunRecord:
            runner_calls.append("run")
            return await original_pair(service, *args, **kwargs)

        async def blocked(
            _producer: EvaluationProducer, selected: SelectedEvaluation
        ) -> PairedRunRecord:
            assert isinstance(selected.plan.payload.get("automatic_trigger_key"), str)
            seen.update(before=h.snapshot(), plan=selected.plan, task=asyncio.current_task())
            entered.set()
            try:
                if cancel:
                    await asyncio.Future()
                raise RuntimeError("101-V producer interrupted")
            except BaseException as exc:
                seen["error"] = exc
                raise

        async def finish(
            owner: RecursiveImprovementCoordinator, prepared: PreparedPair
        ) -> RecursiveImprovementResult:
            try:
                return await original_finish(owner, prepared)
            except BaseException as exc:
                assert exc is seen["error"]
                after = h.snapshot()
                rows = added_controls(seen["before"], after)
                assert len(rows) == 1
                row = rows[0]
                assert (row.namespace, row.record_type, row.record_id, row.state, row.version) == (
                    "IMPROVEMENT",
                    "EVALUATION_PLAN",
                    seen["plan"].record_id,
                    "HELD",
                    seen["plan"].version + 1,
                )
                assert row.payload["reason_code"] == (
                    "EVALUATION_CANCELLED" if cancel else "EVALUATION_INTERRUPTED"
                )
                for table in after:
                    if table != "control_records":
                        assert after[table] == seen["before"][table], table
                assert h.records("IMPROVEMENT_RUNTIME", "RUN") == ()
                seen["after"] = after
                save(
                    "cancel" if cancel else "interrupted",
                    seen["before"],
                    after,
                    same_exception=True,
                    underlying_runner_calls=len(runner_calls),
                )
                raise

        with monkeypatch.context() as patch:
            patch.setattr(EvaluationProducer, "run", blocked)
            patch.setattr(PairedEvaluationService, "run", pair_run)
            patch.setattr(RecursiveImprovementCoordinator, "_evaluate_selected", finish)
            task = asyncio.create_task(h.input("second"))
            try:
                await asyncio.wait_for(entered.wait(), 30)
                if cancel:
                    seen["task"].cancel("101-V cancellation")
                    with pytest.raises(asyncio.CancelledError):
                        await task
                else:
                    assert (await task).error is not None
            finally:
                if not task.done():
                    task.cancel()
                    with suppress(asyncio.CancelledError):
                        await task
        assert runner_calls == [] and h.evaluator.calls == 0 and "after" in seen
        caller_cleanup(h, seen["after"], cancelled=cancel)


async def test_actual_committed_run_repairs_summary_via_exact_context_coordinator_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with harness(tmp_path) as h:
        seen: dict[str, Any] = {}
        original_observe = RecursiveImprovementCoordinator.observe
        original_inner = RecursiveImprovementCoordinator._observe  # pyright: ignore[reportPrivateUsage]
        original_run = EvaluationProducer.run
        original_save = ImprovementObservationResults.save
        calls = {"inner": 0, "producer": 0}

        async def observe(owner: RecursiveImprovementCoordinator, **kwargs: Any) -> Any:
            seen.update(owner=owner, arguments=kwargs.copy(), work=research_work.get())
            return await original_observe(owner, **kwargs)

        async def inner(owner: RecursiveImprovementCoordinator, **kwargs: Any) -> Any:
            calls["inner"] += 1
            return await original_inner(owner, **kwargs)

        async def run(
            producer: EvaluationProducer, selected: SelectedEvaluation
        ) -> PairedRunRecord:
            calls["producer"] += 1
            return await original_run(producer, selected)

        def summary(
            store: ImprovementObservationResults, event: str, result: RecursiveImprovementResult
        ) -> None:
            if result.state.value != "EVALUATED":
                return original_save(store, event, result)
            seen.update(before=h.snapshot(), result=result, event=event)
            try:
                original_save(store, event, result)
            except RuntimeError as exc:
                assert str(exc) == FAULT
                unchanged(h, seen["before"], "summary-fault", committed_run=result.run_id)
                seen["checked"] = True
                raise

        monkeypatch.setattr(RecursiveImprovementCoordinator, "_observe", inner)
        monkeypatch.setattr(EvaluationProducer, "run", run)
        with monkeypatch.context() as patch:
            patch.setattr(RecursiveImprovementCoordinator, "observe", observe)
            patch.setattr(ImprovementObservationResults, "save", summary)
            writes = append_fault(
                patch,
                h,
                lambda r: (
                    r.namespace == "IMPROVEMENT_RUNTIME"
                    and r.record_type == "OBSERVATION_RESULT"
                    and r.record_id == "observation-result:" + seen.get("event", "")
                ),
            )
            failed = await h.input("second")
        assert failed.error is not None and seen["checked"]
        targets = [r for r in writes if r.record_type == "OBSERVATION_RESULT"]
        assert len(targets) == 1 and targets[0].state == "EVALUATED"
        caller_cleanup(h, seen["before"])
        baseline = h.snapshot()
        replay = await h.input("second")
        assert replay.error == failed.error and h.snapshot() == baseline
        sandbox_calls = len(h.sandbox.seen_specs)
        token = research_work.set(seen["work"])
        try:
            recovered = await original_observe(seen["owner"], **seen["arguments"])
            assert recovered is not None
            assert recovered == seen["result"]
            after = h.snapshot()
            rows = added_controls(baseline, after)
            assert len(rows) == 1
            row = rows[0]
            assert (row.namespace, row.record_type, row.record_id, row.state) == (
                "IMPROVEMENT_RUNTIME",
                "OBSERVATION_RESULT",
                "observation-result:" + seen["event"],
                "EVALUATED",
            )
            assert row.payload["result"] == seen["result"].model_dump(mode="json")
            for table in after:
                if table != "control_records":
                    assert after[table] == baseline[table], table
            assert await original_observe(seen["owner"], **seen["arguments"]) == recovered
            assert h.snapshot() == after
        finally:
            research_work.reset(token)
        assert calls == {"inner": 1, "producer": 1} and h.evaluator.calls == 0
        assert len(h.sandbox.seen_specs) == sandbox_calls
        check_reopened(h.runtime.ledger.engine, after)
        save(
            "summary-direct-recovery",
            baseline,
            after,
            calls=calls,
            exact_run_id=recovered.run_id,
            transport_key_still_failed=True,
            scope="Coordinator recovery with captured context; not HTTP fresh-key/resume recovery",
        )
