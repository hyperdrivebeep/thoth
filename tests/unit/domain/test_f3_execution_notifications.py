from __future__ import annotations

from typing import cast

from pydantic import JsonValue

from thoth.protocol.notifications import IMPLEMENTED_NOTIFICATIONS, notifications_for


def test_execution_notification_mapping_covers_active_f3_contract() -> None:
    methods = (
        "execution/start",
        "execution/pause",
        "execution/resume",
        "execution/cancel",
        "execution/retry",
        "execution/observation/link",
        "execution/compensation/propose",
        "execution/invalidate",
    )
    emitted: set[str] = set()
    for method in methods:
        emitted.update(notifications_for(method, cast(dict[str, JsonValue], {})))
    for attempt_state, plan_state in (
        ("SUCCEEDED", "COMPLETED"),
        ("PARTIAL", "PARTIAL"),
        ("FAILED", "PARTIAL"),
        ("UNKNOWN_COMPLETION", "PARTIAL"),
    ):
        emitted.update(
            notifications_for(
                "execution/reconcile",
                cast(
                    dict[str, JsonValue],
                    {
                        "attempt": {"state": attempt_state},
                        "execution": {"state": plan_state},
                    },
                ),
            )
        )
    expected = {
        "execution/created",
        "execution/started",
        "execution/frontierChanged",
        "execution/stepDispatched",
        "execution/attemptStarted",
        "execution/attemptSucceeded",
        "execution/attemptPartial",
        "execution/attemptFailed",
        "execution/unknownCompletion",
        "execution/reconciliationRequired",
        "execution/reconciliationCompleted",
        "execution/cancelRequested",
        "execution/effectChanged",
        "execution/paused",
        "execution/resumed",
        "execution/planPartial",
        "execution/planCompleted",
        "execution/compensationRequired",
        "execution/invalidated",
    }
    assert expected <= emitted
    assert expected <= IMPLEMENTED_NOTIFICATIONS
