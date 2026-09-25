from __future__ import annotations

from typing import cast

from pydantic import JsonValue

from thoth.protocol.notifications import IMPLEMENTED_NOTIFICATIONS, notifications_for


def test_criteria_notification_mapping_covers_f2_catalog() -> None:
    method_results: dict[str, dict[str, object]] = {
        "criteria/compile": {},
        "criteria/field/correct": {"conflict": {}},
        "criteria/profile/apply": {},
        "criteria/revalidate": {},
        "criteria/recalculate": {},
        "criteria/reference/generate": {},
        "criteria/change/propose": {},
        "project/source/disconnect": {"binding": {"state": "REVOKED"}},
    }
    emitted: set[str] = set()
    for method, result in method_results.items():
        emitted.update(notifications_for(method, cast(dict[str, JsonValue], result)))
    expected = {
        "criteria/compiled",
        "criteria/updated",
        "criteria/profileApplied",
        "criteria/readinessUpdated",
        "criteria/conflictUpdated",
        "criteria/recalculated",
        "criteria/referenceUpdated",
        "criteria/changeProposed",
        "criteria/invalidated",
    }
    assert expected <= emitted
    assert expected <= IMPLEMENTED_NOTIFICATIONS
