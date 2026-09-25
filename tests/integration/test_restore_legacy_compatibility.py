from pathlib import Path

import pytest
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_restore_apply_atomicity import CHANGES, handler
from tests.integration.test_restore_preview_contract import prepared, revise

from thoth.domain.canonical import head_set_digest


@pytest.mark.parametrize("route", ("direct", "proposal"))
async def test_legacy_routes_share_strict_publication_and_ignore_caller_role(
    tmp_path: Path, route: str
):
    runtime, model, _accepted, candidates = await prepared(tmp_path)
    try:
        handler(runtime)
        revision, snap = candidates["hypothesis.v1"]
        changed = revise(runtime, revision, snap, *CHANGES["hypothesis.v1"])
        calls = len(model.calls)
        if route == "direct":
            applied = value(
                await runtime.bus.dispatch(
                    request(
                        "revision/restore",
                        "legacy-direct",
                        {
                            "project_id": "p",
                            "entity_type": "HYPOTHESIS",
                            "entity_id": revision.entity_id,
                            "selected_revision_id": revision.revision_id,
                            "expected_current_head": changed.revision_digest,
                            "reason": "Use the same strict contract",
                            "actor_role": "forged-administrator",
                        },
                    )
                )
            )
            assert applied["restore"]["commit"]["disposition"] == "FAST_FORWARD"
        else:
            proposed = value(
                await runtime.bus.dispatch(
                    request(
                        "revision/restore/propose",
                        "proposal",
                        {
                            "project_id": "p",
                            "aggregate_id": revision.entity_id,
                            "target_revision_digest": revision.revision_digest,
                            "current_head_digest": changed.revision_digest,
                            "reason": "Use the same strict contract",
                            "evidence_refs": [],
                            "actor_or_agent_ref": "human:local-user",
                        },
                    )
                )
            )
            change = proposed["change_set"]
            validated = value(
                await runtime.bus.dispatch(
                    request(
                        "revision/changeSet/validate",
                        "validate",
                        {
                            "project_id": "p",
                            "record_id": change["record_id"],
                            "expected_change_set_revision": change["version"],
                        },
                    )
                )
            )
            assert validated["impact_propagation_plan"]["stale_refs"]
            applied = value(
                await runtime.bus.dispatch(
                    request(
                        "revision/changeSet/commit",
                        "commit",
                        {
                            "project_id": "p",
                            "record_id": change["record_id"],
                            "validation_bundle_digest": validated["validation_bundle_digest"],
                            "expected_head_set_digest": head_set_digest(
                                change["payload"]["expected_head_set"]
                            ),
                        },
                    )
                )
            )
            assert applied["change_set"]["state"] == "COMMITTED" and applied["contains_restore"]
        assert applied["status"] == "APPLIED"
        new = runtime.ledger.read_revision_by_digest("p", applied["new_revision_digest"])
        assert new is not None
        assert new.actor.actor_id == "human:local-user" and new.actor.role == "local-operator"
        restored_snapshot = runtime.ledger.read_snapshot(new.snapshot_id)
        assert restored_snapshot is not None and restored_snapshot.content == snap.content
        assert (
            runtime.ledger.read_dependency_states("p")[f"HYPOTHESIS:{new.entity_id}"].value
            == "STALE"
        )
        assert len(model.calls) == calls
    finally:
        runtime.close()
