from __future__ import annotations

from typing import cast

from pydantic import JsonValue

from thoth.protocol.notifications import IMPLEMENTED_NOTIFICATIONS, notifications_for


def test_memory_notification_mapping_covers_active_f5_contract() -> None:
    emitted: set[str] = set()
    for method in (
        "memory/candidate/create",
        "memory/candidate/classify",
        "memory/changeSet/propose",
        "memory/retire/propose",
        "memory/context/build",
        "memory/projection/rebuild",
        "memory/retention/evaluate",
    ):
        emitted.update(notifications_for(method, cast(dict[str, JsonValue], {})))
    for outcome in ("VALIDATED", "HELD", "QUARANTINED", "REJECTED"):
        emitted.update(
            notifications_for(
                "memory/validate",
                cast(dict[str, JsonValue], {"reducer_outcome": outcome}),
            )
        )
    emitted.update(
        notifications_for(
            "memory/revalidate",
            cast(dict[str, JsonValue], {"lifecycle": "SUPERSEDED"}),
        )
    )
    expected = {
        "memory/candidateCreated",
        "memory/classified",
        "memory/validationStarted",
        "memory/validated",
        "memory/revised",
        "memory/held",
        "memory/recallEligibilityChanged",
        "memory/expired",
        "memory/retired",
        "memory/superseded",
        "memory/quarantined",
        "memory/rejected",
        "memory/contextBuilt",
        "memory/projectionRebuilt",
    }
    assert expected <= emitted
    assert expected <= IMPLEMENTED_NOTIFICATIONS
