from __future__ import annotations

from typing import cast

from pydantic import JsonValue

from thoth.protocol.notifications import IMPLEMENTED_NOTIFICATIONS, notifications_for


def test_revision_notification_mapping_covers_f4_contract() -> None:
    emitted: set[str] = set()
    for method in (
        "revision/propose",
        "revision/changeSet/create",
        "revision/branch/create",
        "revision/restore/propose",
        "revision/recompute/request",
        "revision/baseline/prepare",
    ):
        emitted.update(notifications_for(method, cast(dict[str, JsonValue], {})))
    emitted.update(
        notifications_for(
            "revision/changeSet/validate",
            cast(dict[str, JsonValue], {"state": "READY_TO_COMMIT"}),
        )
    )
    emitted.update(
        notifications_for(
            "revision/changeSet/validate",
            cast(dict[str, JsonValue], {"state": "HELD"}),
        )
    )
    emitted.update(
        notifications_for(
            "revision/changeSet/commit",
            cast(
                dict[str, JsonValue],
                {"atomic_commit": True, "contains_restore": True},
            ),
        )
    )
    emitted.update(
        notifications_for(
            "revision/changeSet/commit",
            cast(dict[str, JsonValue], {"atomic_commit": False}),
        )
    )
    emitted.update(
        notifications_for(
            "revision/merge/propose",
            cast(dict[str, JsonValue], {"conflict": {"record_id": "conflict:1"}}),
        )
    )
    emitted.update(notifications_for("revision/merge/resolve", cast(dict[str, JsonValue], {})))
    emitted.update(
        notifications_for(
            "revision/baseline/decide",
            cast(dict[str, JsonValue], {"baseline": {"record_id": "baseline:1"}}),
        )
    )
    emitted.update(
        notifications_for(
            "revision/baseline/decide",
            cast(dict[str, JsonValue], {"baseline_candidate": {"state": "REJECTED"}}),
        )
    )
    expected = {
        "revision/proposed",
        "revision/validated",
        "revision/held",
        "revision/rejected",
        "revision/changeSetCreated",
        "revision/changeSetCommitted",
        "revision/changeSetAborted",
        "revision/headChanged",
        "revision/branchCreated",
        "revision/mergeProposed",
        "revision/conflictOpened",
        "revision/conflictResolved",
        "revision/restoreProposed",
        "revision/restored",
        "revision/projectionStale",
        "revision/recomputeRequested",
        "revision/baselinePrepared",
        "revision/baselineCreated",
        "revision/baselineSuperseded",
        "revision/invalidated",
    }
    assert expected <= emitted
    assert expected <= IMPLEMENTED_NOTIFICATIONS
