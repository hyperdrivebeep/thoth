from pathlib import Path

from tests.atomicity.harness import assert_phase_delta, snapshot
from tests.atomicity.outcome_helpers import prepare_outcome
from tests.integration.scoped_runtime import create_runtime
from tests.integration.storage_coverage_helpers import request, value


async def test_public_plan_revision_binds_current_dag_and_survives_reopen(tmp_path: Path) -> None:
    runtime, ctx = await prepare_outcome(tmp_path, create_series=False)
    project, plan = ctx["project"], ctx["plan"]
    steps = [dict(step) for step in plan["steps"]]
    steps[0]["stop_conditions"] = ["new local checkpoint"]
    command = request(
        "action/plan/revise",
        "revise",
        {
            "project_id": project,
            "plan_id": plan["plan_id"],
            "expected_revision_digest": plan["revision_digest"],
            "patch": {"steps": steps},
            "evidence_refs": ctx["spans"],
            "reason": "Clarify stop condition",
        },
    )
    try:
        revised = value(await runtime.bus.dispatch(command))["plan"]
        assert revised["revision_digest"] != plan["revision_digest"]
        assert revised["steps"][0]["stop_conditions"] == ["new local checkpoint"]
        assert revised["authorization_refs"] == []
        before_replay = snapshot(runtime.ledger.engine)
        assert value(await runtime.bus.dispatch(command))["plan"] == revised
        assert_phase_delta(before_replay, snapshot(runtime.ledger.engine))
        denied = await runtime.bus.dispatch(
            request("action/plan/revise", "stale", dict(command.params.input))
        )
        assert denied.error is not None
        assert snapshot(runtime.ledger.engine)["action_plans"] == before_replay["action_plans"]
    finally:
        runtime.close()
    reopened = create_runtime(tmp_path)
    try:
        current = value(
            await reopened.bus.dispatch(
                request(
                    "action/plan/read", "read", {"project_id": project, "plan_id": plan["plan_id"]}
                )
            )
        )["plan"]
        assert current == revised
    finally:
        reopened.close()
