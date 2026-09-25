"""Normal research execution preserves concurrent edits and canonical attempt history."""

import json
from pathlib import Path
from typing import Any, cast

import pytest
from tests.integration.storage_coverage_helpers import domain_snapshot
from tests.integration.test_hypothesis_prediction_outcome_cycle import (
    prepare_measurement,
    request,
    value,
)

from thoth.adapters.storage.execution import SqliteExecutionStore
from thoth.adapters.storage.hypothesis import SqliteHypothesisStore
from thoth.adapters.storage.test_validity import SqliteTestValidityStore
from thoth.application.services.r2_test_lifecycle import PreparedResearchExecution, R2TestLifecycle
from thoth.apps.runtime import create_runtime
from thoth.domain.behavior_execution import BehaviorExecutionRecord
from thoth.domain.canonical import head_set_digest
from thoth.domain.control_record import ControlRecord
from thoth.domain.hypothesis_full import PredictionRecord
from thoth.domain.sandbox import SandboxExecutionState, SandboxRunSpec
from thoth.domain.test_validity import TestValidityAssessment as ValidityAssessment


@pytest.mark.asyncio
async def test_concurrent_hypothesis_edit_keeps_actual_execution_but_rejects_appraisal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, sandbox, project, thread, _contract = await prepare_measurement(tmp_path, monkeypatch)
    changed: list[str] = []

    async def revise_during_run(spec: SandboxRunSpec) -> None:
        action = value(
            await runtime.bus.dispatch(
                request(
                    "action/read",
                    "running-action",
                    {
                        "project_id": project,
                        "action_id": spec.action_id,
                    },
                )
            )
        )["action"]
        hypothesis_id = action["hypothesis_refs"][0]
        hypothesis = value(
            await runtime.bus.dispatch(
                request(
                    "hypothesis/read",
                    "running-hypothesis",
                    {
                        "project_id": project,
                        "hypothesis_id": hypothesis_id,
                    },
                )
            )
        )["hypothesis"]
        value(
            await runtime.bus.dispatch(
                request(
                    "hypothesis/revise",
                    "concurrent-edit",
                    {
                        "project_id": project,
                        "hypothesis_id": hypothesis_id,
                        "expected_revision_digest": hypothesis["revision_digest"],
                        "patch": {
                            "statement": "The operator changed the hypothesis during measurement"
                        },
                        "evidence_refs": hypothesis["evidence_refs"],
                        "reason": "new operator evidence",
                    },
                )
            )
        )
        changed.append(hypothesis_id)

    sandbox.on_result = revise_during_run
    try:
        analyzed = value(
            await runtime.bus.dispatch(
                request("thread/input", "race", {"project_id": project, "thread_id": thread})
            )
        )
        assert analyzed["r2_closed_loop"]["terminal_state"] == "HOLD"
        assert analyzed["r2_closed_loop"]["reason_code"] == "SANDBOX_HEAD_STALE_AFTER_IO"
        store = SqliteExecutionStore(runtime.ledger.engine)
        attempt = store.read_attempt(project, sandbox.seen_specs[0].attempt_id)
        assert attempt is not None and attempt.state == "SUCCEEDED"
        assert attempt.observation_completeness == "NOT_ADMITTED"
        execution = store.read_execution(project, attempt.plan_execution_id)
        assert execution is not None and execution.state == "PARTIAL"
        hypothesis = value(
            await runtime.bus.dispatch(
                request(
                    "hypothesis/read",
                    "after-race",
                    {
                        "project_id": project,
                        "hypothesis_id": changed[0],
                    },
                )
            )
        )["hypothesis"]
        assert hypothesis["statement"] == "The operator changed the hypothesis during measurement"
        assert hypothesis["empirical_appraisal"] == "UNASSESSED"
        assert hypothesis["test_refs"] == []
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_failed_process_retains_attempt_and_invalid_test_without_support(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, sandbox, project, thread, _contract = await prepare_measurement(
        tmp_path, monkeypatch, states=(SandboxExecutionState.FAILED,)
    )
    try:
        analyzed = value(
            await runtime.bus.dispatch(
                request(
                    "thread/input", "failed-trial", {"project_id": project, "thread_id": thread}
                )
            )
        )
        store = SqliteExecutionStore(runtime.ledger.engine)
        attempt = store.read_attempt(project, sandbox.seen_specs[0].attempt_id)
        assert attempt is not None and attempt.state == "FAILED"
        lifecycle = analyzed["r2_closed_loop"]["test_lifecycle"]
        assert all(item["substantive_update_applied"] is False for item in lifecycle["appraisals"])
        assert all(item["test_validity"] == "INVALID" for item in lifecycle["assessments"])
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_transient_retry_uses_two_real_attempts_and_one_prediction_basis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, sandbox, project, thread, _contract = await prepare_measurement(
        tmp_path,
        monkeypatch,
        states=(SandboxExecutionState.TIMED_OUT, SandboxExecutionState.SUCCEEDED),
        max_transient_retries=1,
    )
    try:
        analyzed = value(
            await runtime.bus.dispatch(
                request("thread/input", "retry-trial", {"project_id": project, "thread_id": thread})
            )
        )
        assert len(sandbox.seen_specs) == 2
        store = SqliteExecutionStore(runtime.ledger.engine)
        attempts = [store.read_attempt(project, spec.attempt_id) for spec in sandbox.seen_specs]
        assert all(attempt is not None for attempt in attempts)
        assert [attempt.state for attempt in attempts if attempt is not None] == [
            "FAILED",
            "SUCCEEDED",
        ]
        assert len({attempt.plan_execution_id for attempt in attempts if attempt is not None}) == 1
        lifecycle = analyzed["r2_closed_loop"]["test_lifecycle"]
        assert all(item["test_validity"] == "VALID" for item in lifecycle["assessments"])
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_prediction_preparation_fault_rolls_back_whole_pre_io_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, sandbox, project, thread, _contract = await prepare_measurement(tmp_path, monkeypatch)
    baseline: list[dict[str, tuple[str, ...]]] = []
    prepare = R2TestLifecycle.prepare
    add = SqliteHypothesisStore.add_prediction
    calls = 0

    def capture(
        self: R2TestLifecycle, *args: Any, **kwargs: Any
    ) -> PreparedResearchExecution | None:
        baseline.append(domain_snapshot(runtime.ledger.engine))
        return prepare(self, *args, **kwargs)

    def fail_second(self: SqliteHypothesisStore, prediction: PredictionRecord) -> None:
        nonlocal calls
        calls += 1
        add(self, prediction)
        if calls == 2:
            raise RuntimeError("injected prediction batch failure")

    try:
        with monkeypatch.context() as patch:
            patch.setattr(R2TestLifecycle, "prepare", capture)
            patch.setattr(SqliteHypothesisStore, "add_prediction", fail_second)
            response = await runtime.bus.dispatch(
                request(
                    "thread/input", "prepare-fault", {"project_id": project, "thread_id": thread}
                )
            )
        assert response.error is not None and calls == 2
        assert sandbox.seen_specs == []
        # The outer Thread finalizer updates its pointer to the already-committed reasoning cycle.
        # All scientific owner state from the failed preparation must remain unchanged.
        actual = domain_snapshot(runtime.ledger.engine)
        prior_controls = set(baseline[0]["control_records"])
        current_controls = set(actual["control_records"])
        assert prior_controls <= current_controls
        added_controls = current_controls - prior_controls
        assert len(added_controls) == 1
        terminal_row = added_controls.pop()
        terminal = ControlRecord.model_validate_json(json.loads(terminal_row)["content_json"])
        assert terminal.namespace == "BEHAVIOR_RUNTIME" and terminal.record_type == "EXECUTION"
        assert terminal.project_id == project and terminal.state == "HELD"
        previous = [
            ControlRecord.model_validate_json(json.loads(row)["content_json"])
            for row in prior_controls
        ]
        running = [
            item
            for item in previous
            if item.namespace == terminal.namespace and item.record_id == terminal.record_id
        ]
        assert len(running) == 1 and running[0].state == "RUNNING"
        assert terminal.version == running[0].version + 1
        assert terminal.supersedes_digest == running[0].record_digest
        before_phase = BehaviorExecutionRecord.model_validate(running[0].payload)
        after_phase = BehaviorExecutionRecord.model_validate(terminal.payload)
        assert after_phase.execution_id == before_phase.execution_id == terminal.record_id
        assert after_phase.thread_id == before_phase.thread_id == thread
        assert after_phase.snapshots == before_phase.snapshots
        assert after_phase.started_at == before_phase.started_at
        assert after_phase.finished_at is not None
        assert after_phase.state == "HELD" and after_phase.reason_code == "BEHAVIOR_EXECUTION_HELD"
        assert after_phase.uses and not after_phase.semantic_truth_certified
        assert all(item.origin in {"BASELINE", "BUILTIN_DEFAULT"} for item in after_phase.snapshots)
        # Remove only this verified append-only phase transition, never a whole namespace/table.
        actual["control_records"] = tuple(
            row for row in actual["control_records"] if row != terminal_row
        )
        assert {key: rows for key, rows in actual.items() if key != "threads"} == {
            key: rows for key, rows in baseline[0].items() if key != "threads"
        }
        work = value(
            await runtime.bus.dispatch(
                request(
                    "thread/read",
                    "after-prepare-fault",
                    {
                        "project_id": project,
                        "thread_id": thread,
                    },
                )
            )
        )
        assert work["working_head_digest"] == head_set_digest(runtime.ledger.read_heads(project))
        persisted = domain_snapshot(runtime.ledger.engine)
    finally:
        runtime.close()
    reopened = create_runtime(tmp_path / "allowed")
    try:
        assert domain_snapshot(reopened.ledger.engine) == persisted
    finally:
        reopened.close()


@pytest.mark.asyncio
async def test_finalization_fault_keeps_real_execution_but_never_replays_it_after_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.integration.test_a02_autonomous_acquisition import StaticModelResolver
    from tests.integration.test_a04_r2_closed_loop import A04R2Model

    from thoth.ports.model import ModelPort

    runtime, sandbox, project, thread, _contract = await prepare_measurement(tmp_path, monkeypatch)
    add = SqliteTestValidityStore.add_assessment
    calls = 0

    def fail_second(self: SqliteTestValidityStore, assessment: ValidityAssessment) -> None:
        nonlocal calls
        calls += 1
        add(self, assessment)
        if calls == 2:
            raise RuntimeError("injected assessment finalization failure")

    try:
        with monkeypatch.context() as patch:
            patch.setattr(SqliteTestValidityStore, "add_assessment", fail_second)
            result = value(
                await runtime.bus.dispatch(
                    request(
                        "thread/input",
                        "finalize-fault",
                        {"project_id": project, "thread_id": thread},
                    )
                )
            )
        assert calls == 2 and len(sandbox.seen_specs) == 1
        assert result["r2_closed_loop"]["terminal_state"] == "HOLD"
        assert result["r2_closed_loop"]["reason_code"] == "RESEARCH_TEST_FINALIZATION_FAILED"
        attempts = SqliteExecutionStore(runtime.ledger.engine)
        attempt = attempts.read_attempt(project, sandbox.seen_specs[0].attempt_id)
        assert attempt is not None and attempt.state == "SUCCEEDED"
        assert attempt.observation_completeness == "NOT_ADMITTED"
        records = SqliteHypothesisStore(runtime.ledger.engine, runtime.ledger).list_hypotheses(
            project
        )
        assert all(
            item.empirical_appraisal == "UNASSESSED" and not item.test_refs for item in records
        )
    finally:
        runtime.close()
    reopened = create_runtime(
        tmp_path / "allowed",
        sandbox_adapter=sandbox,
        model_resolver=StaticModelResolver(cast(ModelPort, A04R2Model())),
    )
    try:
        result = value(
            await reopened.bus.dispatch(
                request(
                    "thread/input", "after-restart", {"project_id": project, "thread_id": thread}
                )
            )
        )
        assert (
            result["r2_closed_loop"]["reason_code"]
            == "RESEARCH_TEST_RESULT_RECONCILIATION_REQUIRED"
        )
        assert len(sandbox.seen_specs) == 1
    finally:
        reopened.close()


@pytest.mark.asyncio
async def test_restore_keeps_raw_snapshot_and_excludes_old_test_from_active_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.integration.test_a02_autonomous_acquisition import StaticModelResolver
    from tests.integration.test_a04_r2_closed_loop import A04R2Model

    from thoth.ports.model import ModelPort

    runtime, sandbox, project, thread, _contract = await prepare_measurement(
        tmp_path, monkeypatch, choose_read_after_test=True
    )
    try:
        result = value(
            await runtime.bus.dispatch(
                request(
                    "thread/input", "before-restore", {"project_id": project, "thread_id": thread}
                )
            )
        )
        lifecycle = result["r2_closed_loop"]["test_lifecycle"]
        prediction = lifecycle["predictions"][0]
        hypothesis_id = prediction["hypothesis_id"]
        before_prediction = runtime.ledger.read_revision_by_digest(
            project, prediction["hypothesis_revision_digest"]
        )
        assert before_prediction is not None
        snapshot = runtime.ledger.read_snapshot(before_prediction.snapshot_id)
        assert snapshot is not None
        saved = snapshot.model_dump(mode="json")
        current_head = runtime.ledger.read_heads(project)[f"HYPOTHESIS:{hypothesis_id}"]
        value(
            await runtime.bus.dispatch(
                request(
                    "revision/restore",
                    "restore-before-test",
                    {
                        "project_id": project,
                        "entity_type": "HYPOTHESIS",
                        "entity_id": hypothesis_id,
                        "selected_revision_id": before_prediction.revision_id,
                        "expected_current_head": current_head,
                        "reason": "restore the pre-test hypothesis state",
                        "actor_id": "human:test-owner",
                        "actor_role": "project-owner",
                    },
                )
            )
        )
        restored = value(
            await runtime.bus.dispatch(
                request(
                    "hypothesis/read",
                    "restored",
                    {"project_id": project, "hypothesis_id": hypothesis_id},
                )
            )
        )["hypothesis"]
        assert restored["empirical_appraisal"] == "UNASSESSED" and restored["prediction_refs"] == []
        original = runtime.ledger.read_snapshot(before_prediction.snapshot_id)
        assert original is not None and original.model_dump(mode="json") == saved
        binding = SqliteHypothesisStore(runtime.ledger.engine, runtime.ledger).list_test_bindings(
            project, prediction["prediction_id"]
        )[0]
        response = await runtime.bus.dispatch(
            request(
                "hypothesis/appraise",
                "old-test-after-restore",
                {
                    "project_id": project,
                    "hypothesis_id": hypothesis_id,
                    "revision_digest": restored["revision_digest"],
                    "evidence_refs": list(binding.observation_refs),
                    "test_assessment_refs": [binding.test_binding_id],
                    "appraisal_scope": {"dataset_version": "trial-v1"},
                },
            )
        )
        assert response.error is not None
        assert "PREDICTION_HYPOTHESIS_BASIS_MISMATCH" in response.error.message
    finally:
        runtime.close()
    reopened = create_runtime(
        tmp_path / "allowed",
        sandbox_adapter=sandbox,
        model_resolver=StaticModelResolver(cast(ModelPort, A04R2Model())),
    )
    try:
        value(
            await reopened.bus.dispatch(
                request(
                    "thread/input", "after-restore", {"project_id": project, "thread_id": thread}
                )
            )
        )
        eligible = {
            item["assessment_id"]
            for item in lifecycle["assessments"]
            if item["hypothesis_id"] != hypothesis_id
        }
        assert set(sandbox.model_assessment_refs[-1]) == eligible
        assert len(sandbox.seen_specs) == 1
    finally:
        reopened.close()


@pytest.mark.asyncio
async def test_duplicate_and_mismatched_test_requests_never_apply_evidence_twice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, _sandbox, project, thread, _contract = await prepare_measurement(tmp_path, monkeypatch)
    try:
        result = value(
            await runtime.bus.dispatch(
                request("thread/input", "real-trial", {"project_id": project, "thread_id": thread})
            )
        )
        lifecycle = result["r2_closed_loop"]["test_lifecycle"]
        assessment = lifecycle["assessments"][0]
        original = SqliteHypothesisStore(runtime.ledger.engine, runtime.ledger).list_test_bindings(
            project, assessment["prediction_id"]
        )[0]
        body: dict[str, object] = {
            "project_id": project,
            "prediction_id": assessment["prediction_id"],
            "execution_ref": assessment["execution_ref"],
            "observation_refs": assessment["observation_refs"],
            "test_validity_assessment_ref": assessment["assessment_id"],
            "test_validity": assessment["test_validity"],
            "prediction_fit": assessment["prediction_fit"],
        }
        heads = dict(runtime.ledger.read_heads(project))
        same = value(
            await runtime.bus.dispatch(request("hypothesis/test/bind", "same-result", body))
        )["test_binding"]
        assert same["test_binding_id"] == original.test_binding_id
        assert dict(runtime.ledger.read_heads(project)) == heads
        for key, patch in (
            ("other-prediction", {"prediction_id": lifecycle["predictions"][1]["prediction_id"]}),
            ("wrong-execution", {"execution_ref": "execution:missing"}),
            (
                "wrong-fit",
                {
                    "prediction_fit": "MISMATCH"
                    if assessment["prediction_fit"] == "MATCH"
                    else "MATCH"
                },
            ),
        ):
            response = await runtime.bus.dispatch(
                request("hypothesis/test/bind", key, {**body, **patch})
            )
            assert response.error is not None
            assert dict(runtime.ledger.read_heads(project)) == heads
        current = value(
            await runtime.bus.dispatch(
                request(
                    "hypothesis/read",
                    "current-for-duplicate",
                    {"project_id": project, "hypothesis_id": assessment["hypothesis_id"]},
                )
            )
        )["hypothesis"]
        response = await runtime.bus.dispatch(
            request(
                "hypothesis/appraise",
                "repeat-appraisal",
                {
                    "project_id": project,
                    "hypothesis_id": current["hypothesis_id"],
                    "revision_digest": current["revision_digest"],
                    "evidence_refs": list(original.observation_refs),
                    "test_assessment_refs": [original.test_binding_id],
                    "appraisal_scope": assessment["scope"],
                },
            )
        )
        assert (
            response.error is not None and "TEST_RESULT_ALREADY_APPLIED" in response.error.message
        )
        assert dict(runtime.ledger.read_heads(project)) == heads
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_selected_execution_never_consumes_an_unselected_approved_r3_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.integration.test_a04_storage_authority import prepare_r3, start_request

    from thoth.adapters.storage.action import SqliteActionStore
    from thoth.application.services.action_service import ActionService
    from thoth.application.services.execution_service import ExecutionService
    from thoth.application.services.revision_service import CommitResult
    from thoth.domain.action_full import ActionPlanRecord
    from thoth.domain.execution_full import PlanExecutionRecord, StepExecutionAttemptRecord

    compose = ActionService.compose_plan

    def with_read_step(
        self: ActionService, *args: Any, **kwargs: Any
    ) -> tuple[ActionPlanRecord, CommitResult]:
        kwargs["step_candidates"] = (
            *kwargs["step_candidates"],
            {
                "step_id": "step:read",
                "state": "READY",
                "inputs": [],
                "effect_vector": {"effect_completeness_confirmed": True, "external_write": False},
            },
        )
        return compose(self, *args, **kwargs)

    monkeypatch.setattr(ActionService, "compose_plan", with_read_step)
    runtime, project, plan, approval = await prepare_r3(tmp_path / "selected")
    start = ExecutionService.start
    owners: list[ExecutionService] = []

    def selected_start(
        self: ExecutionService, *args: Any, **kwargs: Any
    ) -> tuple[PlanExecutionRecord, tuple[StepExecutionAttemptRecord, ...], CommitResult]:
        owners.append(self)
        return start(self, *args, **kwargs, selected_step_ids=("step:read",))

    monkeypatch.setattr(ExecutionService, "start", selected_start)
    try:
        started = value(
            await runtime.bus.dispatch(start_request(runtime, project, plan, "selected-only"))
        )
        execution = PlanExecutionRecord.model_validate(started["execution"])
        store = SqliteExecutionStore(runtime.ledger.engine)
        assert execution.selected_step_ids == ("step:read",)
        assert {
            item.step_id for item in store.list_attempts(project, execution.plan_execution_id)
        } == {"step:read"}
        # Simulate completion of this local read, then exercise the actual frontier advance.
        finished, _commit = owners[0].revise_execution(
            execution,
            updates={"completed_steps": ("step:read",), "state": "PARTIAL"},
            event_type="fixture/readCompleted",
        )
        advanced, new_attempts, _commit = owners[0].resume(
            finished, ActionPlanRecord.model_validate(plan), finished.checkpoint_digest
        )
        assert advanced.state == "COMPLETED" and new_attempts == ()
        preserved = SqliteActionStore(runtime.ledger.engine, runtime.ledger).read_authorization(
            project, approval["authorization_id"]
        )
        assert (
            preserved is not None
            and preserved.state == "APPROVED"
            and preserved.consumed_at is None
        )
        assert {
            item.step_id for item in store.list_attempts(project, execution.plan_execution_id)
        } == {"step:read"}
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_protected_test_read_rejects_execution_projection_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from thoth.domain.execution_full import PlanExecutionRecord

    runtime, _sandbox, project, thread, _contract = await prepare_measurement(tmp_path, monkeypatch)
    try:
        result = value(
            await runtime.bus.dispatch(
                request(
                    "thread/input", "before-drift", {"project_id": project, "thread_id": thread}
                )
            )
        )
        assessment = result["r2_closed_loop"]["test_lifecycle"]["assessments"][0]
        read = SqliteExecutionStore.read_execution

        def drift(
            self: SqliteExecutionStore, project_id: str, identifier: str
        ) -> PlanExecutionRecord | None:
            execution = read(self, project_id, identifier)
            return (
                execution.model_copy(update={"state": "CORRUPTED_PROJECTION"})
                if execution is not None
                else None
            )

        with monkeypatch.context() as patch:
            patch.setattr(SqliteExecutionStore, "read_execution", drift)
            response = await runtime.bus.dispatch(
                request(
                    "hypothesis/test/bind",
                    "drifted-producer",
                    {
                        "project_id": project,
                        "prediction_id": assessment["prediction_id"],
                        "execution_ref": assessment["execution_ref"],
                        "observation_refs": assessment["observation_refs"],
                        "test_validity_assessment_ref": assessment["assessment_id"],
                        "test_validity": assessment["test_validity"],
                        "prediction_fit": assessment["prediction_fit"],
                    },
                )
            )
        assert response.error is not None
        assert "TEST_EXECUTION_CANONICAL_PROJECTION_MISMATCH" in response.error.message
    finally:
        runtime.close()
