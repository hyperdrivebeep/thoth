from __future__ import annotations

from typing import cast

from pydantic import JsonValue

from thoth.protocol.notifications import IMPLEMENTED_NOTIFICATIONS, notifications_for


def test_improvement_notification_mapping_covers_active_contract() -> None:
    emitted: set[str] = set()
    for method in (
        "improvement/propose",
        "improvement/revise",
        "improvement/evaluation/plan",
        "improvement/promotion/prepare",
        "improvement/promotion/decide",
        "improvement/rollback/prepare",
        "improvement/retire/propose",
    ):
        emitted.update(notifications_for(method, cast(dict[str, JsonValue], {})))
    emitted.update(
        notifications_for(
            "improvement/evaluation/assess",
            cast(
                dict[str, JsonValue],
                {"evaluation": {"payload": {"critical_guardrail_failure": True}}},
            ),
        )
    )
    emitted.update(
        notifications_for(
            "improvement/exposure/prepare",
            cast(
                dict[str, JsonValue],
                {"exposure": {"payload": {"requested_exposure_state": "CANARY"}}},
            ),
        )
    )
    expected = {
        "improvement/proposed",
        "improvement/updated",
        "improvement/evaluationPlanned",
        "improvement/evaluated",
        "improvement/exposurePrepared",
        "improvement/canaryPrepared",
        "improvement/guardrailFailed",
        "improvement/promotionPrepared",
        "improvement/promotionDecided",
        "improvement/rollbackPrepared",
        "improvement/retired",
    }
    assert expected <= emitted
    assert expected <= IMPLEMENTED_NOTIFICATIONS
