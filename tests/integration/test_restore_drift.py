from collections.abc import Sequence
from pathlib import Path
from typing import cast

import pytest
from tests.atomicity.harness import assert_phase_delta, failed_command_allowances, snapshot
from tests.integration.storage_coverage_helpers import request
from tests.integration.test_restore_apply_atomicity import CHANGES, handler, input_for
from tests.integration.test_restore_preview_contract import prepared, revise

from thoth.domain.enums import CutoffState, ThreadExecutionState


def _first_ref(value: object) -> str:
    assert isinstance(value, (list, tuple)) and value
    assert all(isinstance(ref, str) for ref in cast(Sequence[object], value))
    first = cast(Sequence[object], value)[0]
    assert isinstance(first, str)
    return first


@pytest.mark.parametrize(
    "drift,reason",
    (
        ("dependent", "RESTORE_PREVIEW_STALE"),
        ("source", "RESTORE_SOURCE_DRIFT"),
        ("active", "RESTORE_ACTIVE_CONSUMER"),
    ),
)
async def test_changed_preview_basis_is_rejected_without_domain_publication(
    tmp_path: Path, drift: str, reason: str
):
    runtime, model, accepted, candidates = await prepared(tmp_path)
    try:
        host = handler(runtime)
        revision, snap = candidates["hypothesis.v1"]
        changed = revise(runtime, revision, snap, *CHANGES["hypothesis.v1"])
        payload = await input_for(runtime, revision, changed, "preview")
        if drift == "dependent":
            action, action_snapshot = candidates["action.v1"]
            revise(
                runtime,
                action,
                action_snapshot,
                "specification",
                {"description": "Independent newer action"},
            )
        elif drift == "source":
            span = host.planner.artifacts.read_evidence(
                _first_ref(snap.content["evidence_refs"])
            )
            assert span is not None
            host.planner.artifacts.update_evidence(
                span.model_copy(update={"cutoff_state": CutoffState.AFTER_CUTOFF})
            )
        else:
            thread = host.planner.threads.read(accepted["thread_id"])
            assert thread is not None
            assert host.planner.threads.update(
                thread.model_copy(
                    update={
                        "execution_state": ThreadExecutionState.RUNNING,
                        "revision": thread.revision + 1,
                    }
                ),
                expected_revision=thread.revision,
            )
        before = snapshot(runtime.ledger.engine)
        calls = len(model.calls)
        command = request("revision/restore/apply", "stale", payload)
        response = await runtime.bus.dispatch(command)
        assert response.error is not None and response.error.data["reason_code"] == reason
        assert_phase_delta(
            before,
            snapshot(runtime.ledger.engine),
            failed_command_allowances(runtime.ledger.engine, command),
        )
        assert len(model.calls) == calls
    finally:
        runtime.close()
