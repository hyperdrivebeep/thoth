"""A sandbox test whose result was not settled is never run again by accident (R2 replay guard).

The same work is the project, the research object and the action. After a run whose finalization
failed, asking again with other input files is held for reconciliation, and the person can clear
the unsettled result (with a reason) so that exactly one new run is allowed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
from tests.integration.test_hypothesis_prediction_outcome_cycle import (
    prepare_measurement,
    request,
    value,
)

from thoth.adapters.storage.execution import SqliteExecutionStore
from thoth.adapters.storage.test_validity import SqliteTestValidityStore
from thoth.application.services.execution_pending import PENDING_RESULT_CLEARED_EVENT
from thoth.apps.runtime import AppRuntime, create_runtime
from thoth.domain.test_validity import TestValidityAssessment as ValidityAssessment

Json = dict[str, Any]


async def held_after_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[AppRuntime, Any, str, str, Json]:
    """One real sandbox run whose finalization fails; then the same question after a restart."""
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
            first = value(
                await runtime.bus.dispatch(
                    request(
                        "thread/input", "rg-first", {"project_id": project, "thread_id": thread}
                    )
                )
            )
        assert first["r2_closed_loop"]["reason_code"] == "RESEARCH_TEST_FINALIZATION_FAILED"
    finally:
        runtime.close()
    reopened = create_runtime(
        tmp_path / "allowed",
        sandbox_adapter=sandbox,
        model_resolver=StaticModelResolver(cast(ModelPort, A04R2Model())),
    )
    again = value(
        await reopened.bus.dispatch(
            request("thread/input", "rg-again", {"project_id": project, "thread_id": thread})
        )
    )
    return reopened, sandbox, project, thread, again["r2_closed_loop"]


@pytest.mark.asyncio
async def test_the_hold_names_the_unsettled_run_the_input_difference_and_the_exits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, sandbox, project, _thread, held = await held_after_restart(tmp_path, monkeypatch)
    try:
        assert held["terminal_state"] == "HOLD"
        assert held["reason_code"] == "RESEARCH_TEST_RESULT_RECONCILIATION_REQUIRED"
        assert len(sandbox.seen_specs) == 1
        pending = held["pending_result"]
        assert pending["attempt_id"] == sandbox.seen_specs[0].attempt_id
        assert pending["attempt_state"] == "SUCCEEDED"
        assert pending["observation_completeness"] == "NOT_ADMITTED"
        difference = pending["input_difference"]
        assert {"same", "previous_count", "current_count", "removed", "added"} <= set(difference)
        # The one way out is to clear and run again (decision 20261003-02); nothing handles a
        # "finish with the existing result" exit, so it is not offered.
        assert pending["exits"] == ["CLEAR_AND_RERUN"]
        assert "finish_possible" not in pending
        # the earlier run and its result are kept as they were
        store = SqliteExecutionStore(runtime.ledger.engine)
        attempt = store.read_attempt(project, pending["attempt_id"])
        assert attempt is not None and attempt.observation_completeness == "NOT_ADMITTED"
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_clearing_records_who_why_and_the_difference_and_allows_exactly_one_new_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, sandbox, project, thread, held = await held_after_restart(tmp_path, monkeypatch)
    try:
        pending = held["pending_result"]
        execution_id = pending["plan_execution_id"]
        base: Json = {
            "project_id": project,
            "plan_execution_id": execution_id,
            "cause_revision_ref": "r2-replay-guard",
            "impact_refs": [],
            "clear_pending_attempt_id": pending["attempt_id"],
            "reason": "The input files were changed on purpose before running again",
            "input_difference": pending["input_difference"],
        }
        # without the revision that was read, or with an old one, nothing is cleared
        for key, extra in (("rg-no-rev", {}), ("rg-old-rev", {"expected_execution_revision": 0})):
            response = await runtime.bus.dispatch(
                request("execution/invalidate", key, {**base, **extra})
            )
            assert response.error is not None
        no_reason = await runtime.bus.dispatch(
            request(
                "execution/invalidate",
                "rg-no-reason",
                {
                    **{k: v for k, v in base.items() if k != "reason"},
                    "expected_execution_revision": pending["execution_revision"],
                },
            )
        )
        assert no_reason.error is not None
        assert len(sandbox.seen_specs) == 1
        cleared = value(
            await runtime.bus.dispatch(
                request(
                    "execution/invalidate",
                    "rg-clear",
                    {**base, "expected_execution_revision": pending["execution_revision"]},
                )
            )
        )
        assert cleared["execution"]["state"] == "INVALIDATED"
        store = SqliteExecutionStore(runtime.ledger.engine)
        records = [
            item
            for item in store.list_audit(project, execution_id)
            if item.event_type == PENDING_RESULT_CLEARED_EVENT
        ]
        assert len(records) == 1
        payload = records[0].payload
        assert payload["attempt_id"] == pending["attempt_id"]
        assert payload["reason"] == base["reason"] and payload["cleared_by"]
        assert payload["input_difference"] == pending["input_difference"]
        assert records[0].created_at is not None
        # the earlier attempt and its result are still there, unchanged
        old = store.read_attempt(project, pending["attempt_id"])
        assert old is not None and old.observation_completeness == "NOT_ADMITTED"
        rerun = value(
            await runtime.bus.dispatch(
                request("thread/input", "rg-rerun", {"project_id": project, "thread_id": thread})
            )
        )
        assert len(sandbox.seen_specs) == 2
        assert rerun["r2_closed_loop"]["terminal_state"] == "COMPLETED", rerun["r2_closed_loop"]
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_a_settled_attempt_cannot_be_cleared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, sandbox, project, thread, _contract = await prepare_measurement(tmp_path, monkeypatch)
    try:
        done = value(
            await runtime.bus.dispatch(
                request("thread/input", "rg-settled", {"project_id": project, "thread_id": thread})
            )
        )
        assert done["r2_closed_loop"]["terminal_state"] == "COMPLETED"
        store = SqliteExecutionStore(runtime.ledger.engine)
        attempt = store.read_attempt(project, sandbox.seen_specs[0].attempt_id)
        assert attempt is not None and attempt.observation_completeness != "NOT_ADMITTED"
        execution = store.read_execution(project, attempt.plan_execution_id)
        assert execution is not None
        response = await runtime.bus.dispatch(
            request(
                "execution/invalidate",
                "rg-settled-clear",
                {
                    "project_id": project,
                    "plan_execution_id": execution.plan_execution_id,
                    "cause_revision_ref": "r2-replay-guard",
                    "impact_refs": [],
                    "clear_pending_attempt_id": attempt.attempt_id,
                    "expected_execution_revision": execution.revision,
                    "reason": "should not be needed",
                },
            )
        )
        assert response.error is not None and "settled" in response.error.message
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_the_held_result_cannot_be_admitted_again_so_finishing_from_it_needs_a_new_rule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Why "finish from the leftover result" is not built on the existing admission.

    A sandbox observation is admitted only while the project HeadSet is the one it ran under. The
    hold itself revises the execution record, which is part of the HeadSet, so the held result's
    basis is already out of date and the same admission would refuse it.
    """
    import json

    from tests.integration.storage_coverage_helpers import domain_snapshot

    from thoth.domain.canonical import head_set_digest
    from thoth.domain.control_record import ControlRecord

    runtime, sandbox, project, _thread, held = await held_after_restart(tmp_path, monkeypatch)
    try:
        attempt_id = held["pending_result"]["attempt_id"]
        rows = domain_snapshot(runtime.ledger.engine)["control_records"]
        held_admission = [
            record
            for record in (
                ControlRecord.model_validate_json(json.loads(row)["content_json"]) for row in rows
            )
            if record.record_id == f"sandbox-admission:{attempt_id}"
        ]
        assert held_admission and held_admission[-1].state == "HELD"
        basis = held_admission[-1].payload["basis"]
        assert isinstance(basis, dict)
        assert basis["head_set_digest"] != head_set_digest(runtime.ledger.read_heads(project))
        assert len(sandbox.seen_specs) == 1
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_the_hold_reaches_the_stored_result_that_the_screen_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The normal (v2) input path publishes the hold; thread/read returns it as execution_hold."""
    from pydantic import BaseModel
    from tests.integration.test_a02_autonomous_acquisition import StaticModelResolver
    from tests.integration.test_a04_r2_closed_loop import A04R2Model
    from tests.integration.test_research_request_v2 import ControlledResearchModel

    from thoth.domain.enums import ModelRole
    from thoth.domain.model import ModelRequest, ModelResult
    from thoth.domain.model_dispatch import CONTROLLED_MODEL_CONTROL
    from thoth.ports.model import ModelPort

    runtime, sandbox, project, thread, _contract = await prepare_measurement(
        tmp_path, monkeypatch, choose_read_after_test=True
    )
    auxiliary = ControlledResearchModel()
    existing = A04R2Model.structured

    async def combined(self: A04R2Model, call: ModelRequest[BaseModel]) -> ModelResult[BaseModel]:
        if call.role in {
            ModelRole.RESEARCH_PLANNER,
            ModelRole.EVIDENCE_RERANKER,
            ModelRole.SEMANTIC_REVIEWER,
            ModelRole.REVIEW_ADJUDICATOR,
            ModelRole.SOURCE_PLANNER,
            ModelRole.HYPOTHESIS_REVIEWER,
        }:
            return await auxiliary.structured(call)
        return await existing(self, call)

    monkeypatch.setattr(A04R2Model, "structured", combined)
    monkeypatch.setattr(A04R2Model, "control_capability", CONTROLLED_MODEL_CONTROL, raising=False)
    add = SqliteTestValidityStore.add_assessment
    calls = 0

    def fail_second(self: SqliteTestValidityStore, assessment: ValidityAssessment) -> None:
        nonlocal calls
        calls += 1
        add(self, assessment)
        if calls == 2:
            raise RuntimeError("injected assessment finalization failure")

    async def ask(active: AppRuntime, key: str) -> None:
        accepted = value(
            await active.bus.dispatch(
                request(
                    "thread/input",
                    key,
                    {
                        "project_id": project,
                        "thread_id": thread,
                        "contract_version": 2,
                        "instruction": "Use the sealed measurement protocol.",
                    },
                )
            )
        )
        await active.bus.drain()
        assert active.bus.read_operation(accepted["operation_id"]) is not None

    try:
        with monkeypatch.context() as patch:
            patch.setattr(SqliteTestValidityStore, "add_assessment", fail_second)
            await ask(runtime, "rgv2-first")
    finally:
        runtime.close()
    reopened = create_runtime(
        tmp_path / "allowed",
        sandbox_adapter=sandbox,
        model_resolver=StaticModelResolver(cast(ModelPort, A04R2Model())),
    )
    try:
        await ask(reopened, "rgv2-again")
        assert len(sandbox.seen_specs) == 1
        read = value(
            await reopened.bus.dispatch(
                request(
                    "thread/read",
                    "rgv2-read",
                    {"project_id": project, "thread_id": thread, "contract_version": 2},
                )
            )
        )
        assert read["answer_outcome"]["state"] == "HOLD", read.get("answer_outcome")
        shown = read["execution_hold"]
        assert shown["exits"] == ["CLEAR_AND_RERUN"]
        assert shown["attempt_state"] == "SUCCEEDED"
        store = SqliteExecutionStore(reopened.ledger.engine)
        assert shown["attempt_id"] == sandbox.seen_specs[0].attempt_id
        execution = store.read_execution(project, shown["plan_execution_id"])
        assert execution is not None and execution.revision == shown["execution_revision"]
    finally:
        reopened.close()
