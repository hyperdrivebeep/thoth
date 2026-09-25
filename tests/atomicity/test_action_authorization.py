from pathlib import Path

import pytest
from tests.atomicity.harness import assert_phase_delta, failed_command_allowances, snapshot
from tests.integration.scoped_runtime import create_runtime
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_a03_storage_atomicity import setup

from thoth.adapters.storage.action import SqliteActionStore
from thoth.domain.action_full import ActionPortfolioRecord


async def test_selected_action_and_portfolio_publish_together(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, ctx = await setup(tmp_path)
    try:
        generated = value(
            await runtime.bus.dispatch(
                request(
                    "action/generate",
                    "actions",
                    {
                        "project_id": ctx["project"],
                        "object_id": ctx["object"],
                        "decision_need": "Compare bounded alternatives",
                        "evidence_scope": ctx["spans"],
                    },
                )
            )
        )
        action_ids = [row["action_id"] for row in generated["actions"]]
        portfolio = value(
            await runtime.bus.dispatch(
                request(
                    "action/portfolio/compose",
                    "portfolio",
                    {
                        "project_id": ctx["project"],
                        "object_id": ctx["object"],
                        "action_ids": action_ids,
                        "decision_need": "Choose an alternative",
                    },
                )
            )
        )["portfolio"]
        command = request(
            "action/select",
            "fault",
            {
                "project_id": ctx["project"],
                "portfolio_id": portfolio["portfolio_id"],
                "action_id": action_ids[0],
                "decision_context": {"reason": "Explicit comparison"},
                "actor_or_agent_ref": "human:local-user",
                "expected_portfolio_revision": portfolio["revision_digest"],
            },
        )
        before = snapshot(runtime.ledger.engine)
        original = SqliteActionStore.add_portfolio

        def fail(store: SqliteActionStore, record: ActionPortfolioRecord) -> None:
            original(store, record)
            raise RuntimeError("selection portfolio fault")

        with monkeypatch.context() as patch:
            patch.setattr(SqliteActionStore, "add_portfolio", fail)
            response = await runtime.bus.dispatch(command)
        assert response.error is not None
        after = snapshot(runtime.ledger.engine)
        assert after["semantic_revisions"] == before["semantic_revisions"]
        assert after["action_records"] == before["action_records"]
        allowed = failed_command_allowances(
            runtime.ledger.engine,
            command,
            expected_reads=(f"revision:{portfolio['revision_digest']}",),
        )
        assert_phase_delta(before, after, allowed)
    finally:
        runtime.close()
    reopened = create_runtime(tmp_path)
    try:
        assert_phase_delta(before, snapshot(reopened.ledger.engine), allowed)
        selected = value(
            await reopened.bus.dispatch(
                request("action/select", "retry", dict(command.params.input))
            )
        )
        assert selected["action"]["proposal_state"] == "SELECTED"
        assert selected["portfolio"]["selected_action_ref"] == action_ids[0]
        assert selected["authorization_implied"] is False
    finally:
        reopened.close()
