from __future__ import annotations

from typing import cast

from pydantic import JsonValue

from thoth.protocol.notifications import IMPLEMENTED_NOTIFICATIONS, notifications_for


def test_action_notification_mapping_covers_active_f3_contract() -> None:
    methods = (
        "action/generate",
        "action/create",
        "action/revise",
        "action/portfolio/compose",
        "action/portfolio/evaluate",
        "action/recommend",
        "action/select",
        "action/plan/compose",
        "action/plan/revise",
        "action/plan/revalidate",
        "action/step/add",
        "action/step/revise",
        "action/impact/recalculate",
        "action/policy/classify",
        "action/authorization/prepare",
        "action/compensation/create",
    )
    emitted: set[str] = set()
    for method in methods:
        emitted.update(notifications_for(method, cast(dict[str, JsonValue], {})))
    emitted.update(
        notifications_for(
            "action/authorization/decide",
            cast(dict[str, JsonValue], {"authorization": {"state": "APPROVED"}}),
        )
    )
    emitted.update(
        notifications_for(
            "action/authorization/decide",
            cast(dict[str, JsonValue], {"authorization": {"state": "EXPIRED"}}),
        )
    )
    expected = {
        "action/generated",
        "action/created",
        "action/updated",
        "action/portfolioUpdated",
        "action/evaluated",
        "action/recommended",
        "action/selected",
        "action/planComposed",
        "action/planUpdated",
        "action/frontierChanged",
        "action/impactChanged",
        "action/policyChanged",
        "action/authorizationPrepared",
        "action/authorizationDecided",
        "action/authorizationStale",
        "action/authorizationExpired",
        "action/compensationCreated",
        "action/invalidated",
    }
    assert expected <= emitted
    assert expected <= IMPLEMENTED_NOTIFICATIONS
