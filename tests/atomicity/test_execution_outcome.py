from pathlib import Path

from tests.atomicity.harness import assert_phase_delta, failed_command_allowances, snapshot
from tests.atomicity.outcome_helpers import prepare_outcome
from tests.integration.storage_coverage_helpers import request, value


async def test_outcome_series_does_not_require_execution_but_rejects_non_plan_revision(
    tmp_path: Path,
) -> None:
    runtime, ctx = await prepare_outcome(tmp_path, create_series=False)
    try:
        before = snapshot(runtime.ledger.engine)
        invalid = request(
            "outcome/series/create",
            "not-a-plan",
            {
                **ctx["series_input"],
                "action_plan_revision_digest": ctx["action"]["revision_digest"],
            },
        )
        rejected = await runtime.bus.dispatch(invalid)
        assert rejected.error is not None, "An ACTION record is not an ActionPlan"
        assert_phase_delta(
            before,
            snapshot(runtime.ledger.engine),
            failed_command_allowances(runtime.ledger.engine, invalid),
        )
        created = value(
            await runtime.bus.dispatch(
                request("outcome/series/create", "valid-plan", ctx["series_input"])
            )
        )["series"]
        assert created["planned_execution_ref"] is None
        assert created["phase_states"] == {"INTERIM": "WAITING_OBSERVATION"}
    finally:
        runtime.close()
