"""100-V: exposure/service publication boundaries, before any service refactor.

All faults run the real write first. Whole domain snapshots include control_records; only
the existing operation bookkeeping exclusions apply. Prepare uses an already stored candidate.
Automatic completion is measured at the outer finalization transaction, after research/model
work has already happened. No production code, fixture, model endpoint or server is changed.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from tests.integration.behavior_exposure_helpers import ExposureHarness, exposure_harness
from tests.integration.storage_coverage_helpers import domain_snapshot, value
from tests.integration.test_m5_improvement_handlers_atomicity import Snapshot, check_reopened

from thoth.adapters.storage.behavior_execution import SqliteBehaviorExecutionStore
from thoth.adapters.storage.control_record import SqliteControlRecordStore
from thoth.application.commands.behavior_execution import BehaviorThreadEntry
from thoth.application.services.behavior_candidate_service import BehaviorCandidateService
from thoth.application.services.control_record_service import ControlRecordService
from thoth.application.services.improvement_exposure_service import ImprovementExposureService
from thoth.domain.behavior_artifact import BehaviorArtifact
from thoth.domain.behavior_execution import (
    BehaviorBaselineRevision,
    BehaviorExecutionRecord,
    BehaviorExposureRecord,
)
from thoth.domain.control_record import ControlRecord

OUT = Path(__file__).resolve().parents[2] / "outputs/refactor-step100-20261015"
FAULT = "100-V injected after targeted service write"


@dataclass
class Writes:
    active: bool = True
    hits: int = 0
    order: list[str] = field(default_factory=list)
    staged: list[ControlRecord] = field(default_factory=list)
    controls: list[ControlRecord] = field(default_factory=list)
    exposures: list[BehaviorExposureRecord] = field(default_factory=list)
    baselines: list[BehaviorBaselineRevision] = field(default_factory=list)
    candidates: list[str] = field(default_factory=list)


def watch(
    patch: pytest.MonkeyPatch,
    h: ExposureHarness,
    control_target: Callable[[ControlRecord], bool],
    *,
    fail_control: bool = False,
    fail_baseline: bool = False,
    active: bool = True,
) -> Writes:
    seen = Writes(active=active)
    stage_original = ControlRecordService.stage
    control_original = SqliteControlRecordStore.append
    exposure_original = SqliteBehaviorExecutionStore.append
    rollback_original = SqliteBehaviorExecutionStore.rollback_baseline
    candidate_original = BehaviorCandidateService.create_candidate

    def stage(service: ControlRecordService, **kwargs: Any) -> ControlRecord:
        record = stage_original(service, **kwargs)
        if seen.active and control_target(record):
            seen.staged.append(record)
        return record

    def append_control(store: SqliteControlRecordStore, record: ControlRecord) -> None:
        control_original(store, record)
        if not seen.active or record.project_id != h.pair.project:
            return
        seen.controls.append(record)
        seen.order.append(f"CONTROL/{record.namespace}/{record.record_type}/{record.state}")
        identities = {
            (r.namespace, r.record_type, r.record_id, r.control_revision_id) for r in seen.staged
        }
        if (
            fail_control
            and (record.namespace, record.record_type, record.record_id, record.control_revision_id)
            in identities
        ):
            seen.hits += 1
            raise RuntimeError(FAULT)

    def append_exposure(
        store: SqliteBehaviorExecutionStore,
        record: BehaviorExposureRecord,
        *,
        expected_revision: int,
    ) -> None:
        exposure_original(store, record, expected_revision=expected_revision)
        if seen.active and record.spec.project_id == h.pair.project:
            seen.exposures.append(record)
            seen.order.append(f"EXPOSURE/{record.state}")

    def rollback(
        store: SqliteBehaviorExecutionStore,
        record: BehaviorBaselineRevision,
        *,
        expected_revision: int,
    ) -> None:
        rollback_original(store, record, expected_revision=expected_revision)
        if (
            seen.active
            and record.project_id == h.pair.project
            and record.exposure_ref == h.exposure["spec"]["exposure_id"]
            and record.change_kind == "ROLLED_BACK"
        ):
            seen.baselines.append(record)
            seen.order.append("BASELINE/ROLLED_BACK")
            if fail_baseline:
                seen.hits += 1
                raise RuntimeError(FAULT)

    def candidate(service: BehaviorCandidateService, **kwargs: Any) -> BehaviorArtifact:
        result = candidate_original(service, **kwargs)
        if seen.active and result.project_id == h.pair.project:
            seen.candidates.append(result.artifact_id)
            seen.order.append("CANDIDATE/RETURNED")
        return result

    patch.setattr(ControlRecordService, "stage", stage)
    patch.setattr(SqliteControlRecordStore, "append", append_control)
    patch.setattr(SqliteBehaviorExecutionStore, "append", append_exposure)
    patch.setattr(SqliteBehaviorExecutionStore, "rollback_baseline", rollback)
    patch.setattr(BehaviorCandidateService, "create_candidate", candidate)
    return seen


def current(h: ExposureHarness) -> BehaviorExposureRecord:
    record = SqliteBehaviorExecutionStore(h.pair.runtime.ledger.engine).read(
        h.pair.project, h.exposure["spec"]["exposure_id"]
    )
    assert record is not None
    return record


def baseline(h: ExposureHarness) -> BehaviorBaselineRevision:
    values = SqliteBehaviorExecutionStore(h.pair.runtime.ledger.engine).baselines(
        h.pair.project, h.pair.binding.component, "LOCAL"
    )
    assert len(values) == 1
    return values[0]


def completion_target(h: ExposureHarness, record: ControlRecord) -> bool:
    return (
        record.project_id == h.pair.project
        and record.namespace == "IMPROVEMENT"
        and record.record_type == "EVALUATION_PLAN"
        and record.record_id == h.pair.plan["record_id"]
        and record.state == "EVALUATED"
        and record.payload.get("promotion_eligible") is True
        and record.payload.get("runtime_exposure_ref") == h.exposure["spec"]["exposure_id"]
    )


def save(name: str, before: Snapshot, after: Snapshot, writes: Writes, **details: object) -> None:
    def sha(snapshot: Snapshot) -> str:
        return hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()

    added = set(after["control_records"]) - set(before["control_records"])
    rows = [ControlRecord.model_validate_json(json.loads(row)["content_json"]) for row in added]
    data = {
        "before_sha256": sha(before),
        "after_sha256": sha(after),
        "changed_tables": [table for table in before if before[table] != after[table]],
        "fault_hits": writes.hits,
        "write_order": writes.order,
        "exposure_writes": [
            {"exposure_id": r.spec.exposure_id, "revision": r.revision, "state": r.state}
            for r in writes.exposures
        ],
        "baseline_writes": [
            {"revision": r.revision, "change_kind": r.change_kind, "exposure_ref": r.exposure_ref}
            for r in writes.baselines
        ],
        "control_writes": [
            {
                "namespace": r.namespace,
                "record_type": r.record_type,
                "record_id": r.record_id,
                "state": r.state,
                "version": r.version,
            }
            for r in writes.controls
        ],
        "added_control_rows": [
            {
                "namespace": r.namespace,
                "record_type": r.record_type,
                "record_id": r.record_id,
                "state": r.state,
            }
            for r in rows
        ],
        **details,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"v100-{name}.json").write_text(
        json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n"
    )


def unchanged(
    h: ExposureHarness, before: Snapshot, writes: Writes, name: str, **info: object
) -> None:
    after = domain_snapshot(h.pair.runtime.ledger.engine)
    save(name, before, after, writes, **info)
    assert after == before, "HOLD_DEFECT: partial publication after service storage failure"
    check_reopened(h.pair.runtime.ledger.engine, before)


async def ready_action(h: ExposureHarness, action: str) -> BehaviorExposureRecord:
    await h.arm()
    value(await h.call("thread/input", "100-v-trial", {"thread_id": h.thread}))
    record = current(h)
    assert record.state == "ACTIVE" and record.request_count == 1
    if action == "rollback":
        await h.finish_trial()
        promotion = await h.prepare_baseline()
        approved = value(await h.approve_baseline(promotion))
        assert approved["current_state_mutated"] is True
        assert baseline(h).content_digest == record.spec.candidate_digest
        record = current(h)
        assert record.state == "COMPLETED"
    return record


def action_body(
    h: ExposureHarness, action: str, record: BehaviorExposureRecord
) -> dict[str, object]:
    body: dict[str, object] = {
        "exposure_id": record.spec.exposure_id,
        "expected_revision": record.revision,
    }
    if action == "rollback":
        body.update(actor_ref="human:policy-owner", role_assignment_ref=h.role)
    return body


def action_order(action: str) -> list[str]:
    if action == "complete":
        return [
            "EXPOSURE/COMPLETED",
            "CONTROL/IMPROVEMENT/CANARY_RUN/COMPLETED",
            "CONTROL/IMPROVEMENT/EVALUATION_PLAN/EVALUATED",
        ]
    return [
        "EXPOSURE/ROLLED_BACK",
        "CONTROL/IMPROVEMENT/CANARY_RUN/ROLLED_BACK",
        "BASELINE/ROLLED_BACK",
    ]


@pytest.mark.parametrize("stage", ["SHADOW", "CANARY"])
async def test_prepare_projection_fault_rolls_back_new_exposure_and_keeps_reused_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    async with exposure_harness(tmp_path, stage=stage) as h:
        previous = current(h)
        assert previous.state == "PREPARED"
        spec = previous.spec
        before = domain_snapshot(h.pair.runtime.ledger.engine)

        def target(r: ControlRecord) -> bool:
            return (
                r.project_id == h.pair.project
                and r.namespace == "IMPROVEMENT"
                and r.record_type == ("SHADOW_RUN" if stage == "SHADOW" else "CANARY_RUN")
                and r.record_id != spec.exposure_id
                and r.state == "PREPARED"
                and r.payload.get("improvement_revision_id") == h.pair.proposal["record_id"]
            )

        with monkeypatch.context() as patch:
            writes = watch(patch, h, target, fail_control=True)
            failed = await h.call(
                "improvement/exposure/prepare",
                "100-v-prepare",
                {
                    "improvement_revision_id": h.pair.proposal["record_id"],
                    "evaluation_plan_id": h.pair.plan["record_id"],
                    "requested_exposure_state": stage,
                    "scope": {"environment": "LOCAL", "thread_id": h.thread},
                    "duration_budget": {
                        "duration_seconds": spec.duration_seconds,
                        "max_requests": spec.max_requests,
                        "max_model_calls": spec.max_model_calls,
                        "max_billed_cost_microunits": spec.max_billed_cost_microunits,
                    },
                    "stop_rollback_contract": {"rollback_target_digest": spec.baseline_digest},
                },
            )
        assert failed.error is not None
        assert writes.hits == 1 and len(writes.staged) == 1 and len(writes.exposures) == 1
        assert writes.candidates == [spec.candidate_artifact_ref]
        assert writes.order == [
            "CANDIDATE/RETURNED",
            "EXPOSURE/PREPARED",
            f"CONTROL/IMPROVEMENT/{stage}_RUN/PREPARED",
        ]
        assert writes.exposures[0].spec.exposure_id == writes.staged[0].record_id
        assert (
            writes.staged[0].payload["runtime_record_digest"] == writes.exposures[0].record_digest
        )
        assert current(h) == previous
        unchanged(h, before, writes, f"prepare-{stage}", candidate_reused=True)


async def test_manual_completion_evaluation_fault_preserves_active_exposure_and_old_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with exposure_harness(tmp_path) as h:
        previous = await ready_action(h, "complete")
        before = domain_snapshot(h.pair.runtime.ledger.engine)
        model_calls = len(h.model.guidance)
        with monkeypatch.context() as patch:
            writes = watch(patch, h, lambda r: completion_target(h, r), fail_control=True)
            failed = await h.call(
                "improvement/exposure/complete",
                "100-v-complete",
                action_body(h, "complete", previous),
            )
        assert failed.error is not None and writes.hits == 1 and len(writes.staged) == 1
        assert writes.order == action_order("complete")
        assert (
            writes.staged[0].payload["runtime_exposure_digest"] == writes.exposures[0].record_digest
        )
        assert current(h) == previous
        assert len(h.model.guidance) == model_calls
        unchanged(h, before, writes, "manual-complete", extra_model_calls=0)


async def test_automatic_completion_fault_rolls_back_only_the_outer_finalization_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with exposure_harness(tmp_path, requests=1) as h:
        await h.arm()
        boundary: list[Snapshot] = []
        terminal_records: list[BehaviorExecutionRecord] = []
        auto_calls: list[str] = []
        # The approved fault boundary is this private terminal writer (same as recovery tests).
        original_persist = BehaviorThreadEntry._persist  # pyright: ignore[reportPrivateUsage]
        original_complete = ImprovementExposureService.complete_observed
        with monkeypatch.context() as patch:
            writes = watch(
                patch, h, lambda r: completion_target(h, r), fail_control=True, active=False
            )

            def persist(entry: BehaviorThreadEntry, record: BehaviorExecutionRecord) -> None:
                if record.project_id == h.pair.project and record.state == "COMPLETED":
                    assert boundary == []
                    boundary.append(domain_snapshot(h.pair.runtime.ledger.engine))
                    terminal_records.append(record)
                    writes.active = True
                original_persist(entry, record)

            def complete(
                service: ImprovementExposureService, record: BehaviorExposureRecord
            ) -> BehaviorExposureRecord:
                auto_calls.append(record.spec.exposure_id)
                writes.order.append("AUTO/COMPLETE_OBSERVED")
                return original_complete(service, record)

            patch.setattr(BehaviorThreadEntry, "_persist", persist)
            patch.setattr(ImprovementExposureService, "complete_observed", complete)
            failed = await h.call("thread/input", "100-v-auto", {"thread_id": h.thread})
        assert failed.error is not None
        assert len(boundary) == len(terminal_records) == 1
        assert auto_calls == [h.exposure["spec"]["exposure_id"]]
        assert writes.hits == 1 and len(writes.staged) == 1
        assert writes.order == [
            "CONTROL/BEHAVIOR_RUNTIME/EXECUTION/COMPLETED",
            "EXPOSURE/ACTIVE",
            "CONTROL/IMPROVEMENT/CANARY_RUN/ACTIVE",
            "AUTO/COMPLETE_OBSERVED",
            *action_order("complete"),
        ]
        assert writes.controls[0].record_id == terminal_records[0].execution_id
        assert len(h.model.guidance) > 0
        unchanged(
            h,
            boundary[0],
            writes,
            "auto-complete",
            boundary="before original terminal _persist in outer finalization",
            research_and_model_already_executed=True,
            terminal_execution_id=terminal_records[0].execution_id,
            auto_complete_calls=len(auto_calls),
        )


async def test_published_baseline_rollback_fault_preserves_completed_exposure_and_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with exposure_harness(tmp_path) as h:
        previous = await ready_action(h, "rollback")
        published = baseline(h)
        before = domain_snapshot(h.pair.runtime.ledger.engine)
        with monkeypatch.context() as patch:
            writes = watch(patch, h, lambda _r: False, fail_baseline=True)
            failed = await h.call(
                "improvement/exposure/rollback",
                "100-v-rollback",
                action_body(h, "rollback", previous),
            )
        assert failed.error is not None and writes.hits == 1
        assert writes.order == action_order("rollback")
        assert len(writes.baselines) == 1
        restored = writes.baselines[0]
        assert (
            restored.scope_digest,
            restored.revision,
            restored.previous_digest,
            restored.content_digest,
        ) == (
            published.scope_digest,
            published.revision + 1,
            published.content_digest,
            previous.spec.baseline_digest,
        )
        assert current(h) == previous and baseline(h) == published
        unchanged(h, before, writes, "rollback")


def successful_delta(before: Snapshot, after: Snapshot, writes: Writes, action: str) -> None:
    changed = {"control_records", "behavior_exposure_history"}
    if action == "rollback":
        changed.add("behavior_baseline_history")
    for table in before:
        if table not in changed:
            assert before[table] == after[table], table
        assert set(before[table]).issubset(after[table]), table
    controls_added = set(after["control_records"]) - set(before["control_records"])
    parsed = [
        ControlRecord.model_validate_json(json.loads(row)["content_json"]) for row in controls_added
    ]
    # ControlRecord.payload is dict[str, object]: its tuples and nested models are stored as
    # JSON arrays/objects by SqliteControlRecordStore.append. Compare every persisted field,
    # including all digests, in that exact representation (without dropping any fields).
    stored_json = {r.control_revision_id: r.model_dump(mode="json") for r in parsed}
    written_json = {r.control_revision_id: r.model_dump(mode="json") for r in writes.controls}
    representation = {
        "native_model_equal": {r.control_revision_id: r for r in parsed}
        == {r.control_revision_id: r for r in writes.controls},
        "complete_stored_json_equal": stored_json == written_json,
        "written_payload_types": [
            {k: type(v).__name__ for k, v in r.payload.items()} for r in writes.controls
        ],
        "stored_payload_types": [
            {k: type(v).__name__ for k, v in r.payload.items()} for r in parsed
        ],
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"v100-{action}-storage-representation.json").write_text(
        json.dumps(representation, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    assert stored_json == written_json
    assert all(r.namespace == "IMPROVEMENT" for r in parsed)
    assert len(parsed) == (2 if action == "complete" else 1)
    history = set(after["behavior_exposure_history"]) - set(before["behavior_exposure_history"])
    exposures = [
        BehaviorExposureRecord.model_validate_json(json.loads(row)["content_json"])
        for row in history
    ]
    assert len(exposures) == 1 and exposures[0] == writes.exposures[0]
    exposure = exposures[0]
    for row in parsed:
        if row.record_type == "CANARY_RUN":
            assert row.record_id == exposure.spec.exposure_id
            assert row.state == exposure.state
            assert row.payload["runtime_record_digest"] == exposure.record_digest
        else:
            assert action == "complete" and row.record_type == "EVALUATION_PLAN"
            assert row.record_id == exposure.spec.evaluation_plan_ref
            assert row.state == "EVALUATED" and row.payload["promotion_eligible"] is True
            assert row.payload["runtime_exposure_ref"] == exposure.spec.exposure_id
            assert row.payload["runtime_exposure_digest"] == exposure.record_digest
    baseline_rows = set(after["behavior_baseline_history"]) - set(
        before["behavior_baseline_history"]
    )
    assert len(baseline_rows) == (1 if action == "rollback" else 0)
    if action == "rollback":
        restored = BehaviorBaselineRevision.model_validate_json(
            json.loads(next(iter(baseline_rows)))["content_json"]
        )
        assert restored == writes.baselines[0]
        assert restored.exposure_ref == exposure.spec.exposure_id
        assert restored.content_digest == exposure.spec.baseline_digest
        assert restored.change_kind == "ROLLED_BACK"


@pytest.mark.parametrize("action", ["complete", "rollback"])
async def test_service_failure_replays_without_reentry_and_fresh_key_publishes_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    async with exposure_harness(tmp_path) as h:
        previous = await ready_action(h, action)
        before = domain_snapshot(h.pair.runtime.ledger.engine)
        body = action_body(h, action, previous)
        method = "improvement/exposure/" + action
        entered: list[str] = []
        original = getattr(ImprovementExposureService, action)

        def entry(service: ImprovementExposureService, *args: Any, **kwargs: Any) -> Any:
            entered.append(action)
            return original(service, *args, **kwargs)

        monkeypatch.setattr(ImprovementExposureService, action, entry)
        model_calls = len(h.model.guidance)
        with monkeypatch.context() as patch:
            writes = watch(
                patch,
                h,
                lambda r: completion_target(h, r),
                fail_control=action == "complete",
                fail_baseline=action == "rollback",
            )
            failed = await h.call(method, "100-v-replay", body)
        assert failed.error is not None and writes.hits == 1
        assert writes.order == action_order(action)
        unchanged(h, before, writes, f"{action}-retry-fault")
        with monkeypatch.context() as patch:
            replay_writes = watch(patch, h, lambda r: completion_target(h, r))
            replay = await h.call(method, "100-v-replay", body)
        assert replay.error == failed.error and entered == [action]
        assert replay_writes.order == [] and replay_writes.staged == []
        unchanged(h, before, replay_writes, f"{action}-same-key", service_reentry=0)
        with monkeypatch.context() as patch:
            retried_writes = watch(patch, h, lambda r: completion_target(h, r))
            retried = value(await h.call(method, "100-v-new-key", body))
        assert entered == [action, action]
        assert len(h.model.guidance) == model_calls
        assert retried_writes.order == action_order(action)
        assert retried_writes.hits == 0
        after = domain_snapshot(h.pair.runtime.ledger.engine)
        successful_delta(before, after, retried_writes, action)
        assert retried["exposure"] == retried_writes.exposures[0].model_dump(mode="json")
        assert current(h).revision == previous.revision + 1
        check_reopened(h.pair.runtime.ledger.engine, after)
        save(f"{action}-new-key", before, after, retried_writes, extra_model_calls=0)
