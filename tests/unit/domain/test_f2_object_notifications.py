from __future__ import annotations

from typing import cast

from pydantic import JsonValue

from thoth.protocol.notifications import IMPLEMENTED_NOTIFICATIONS, notifications_for


def test_object_notification_mapping_covers_f2_catalog() -> None:
    methods = (
        "object/materialize",
        "object/frame/revise",
        "object/profile/apply",
        "object/facet/update",
        "object/relation/add",
        "object/relation/remove",
        "object/revalidate",
        "object/work/replan",
        "object/split/propose",
        "object/merge/propose",
        "object/attention/acknowledge",
        "object/followup/create",
    )
    emitted: set[str] = set()
    for method in methods:
        result: dict[str, object] = {}
        if method in {"object/materialize", "object/followup/create"}:
            result = {"object": {"object_id": "object:1"}}
        if method == "object/attention/acknowledge":
            result = {"attention": {"state": "CLEARED"}}
        emitted.update(notifications_for(method, cast(dict[str, JsonValue], result)))
    expected = {
        "object/candidateCreated",
        "object/materialized",
        "object/updated",
        "object/profileChanged",
        "object/workModeChanged",
        "object/blockerChanged",
        "object/relationChanged",
        "object/attentionRaised",
        "object/attentionCleared",
        "object/resolutionChanged",
        "object/verificationChanged",
        "object/invalidated",
        "object/splitProposed",
        "object/mergeProposed",
    }
    assert expected <= emitted
    assert expected <= IMPLEMENTED_NOTIFICATIONS
