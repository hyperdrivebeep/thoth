from __future__ import annotations

from typing import cast

from pydantic import JsonValue

from thoth.protocol.notifications import (
    IMPLEMENTED_NOTIFICATIONS,
    notifications_for,
    notifications_for_internal_event,
)


def test_revision_domain_transitions_and_authorization_consumption() -> None:
    emitted = set(
        notifications_for(
            "revision/changeSet/commit",
            cast(
                dict[str, JsonValue],
                {
                    "atomic_commit": True,
                    "domain_transitions": [
                        "action/retired",
                        "hypothesis/retired",
                        "memory/committed",
                        "memory/conflictDetected",
                        "object/completed",
                        "object/closed",
                        "object/dispositionChanged",
                        "object/superseded",
                    ],
                },
            ),
        )
    )
    emitted.update(
        notifications_for(
            "execution/resume",
            cast(
                dict[str, JsonValue],
                {"new_attempts": [{"authorization_digest": "a" * 64}]},
            ),
        )
    )
    expected = {
        "action/authorizationConsumed",
        "action/retired",
        "hypothesis/retired",
        "memory/committed",
        "memory/conflictDetected",
        "object/completed",
        "object/closed",
        "object/dispositionChanged",
        "object/superseded",
    }
    assert expected <= emitted
    assert expected <= IMPLEMENTED_NOTIFICATIONS


def test_authenticated_internal_callback_notification_contract() -> None:
    events = {
        "execution.attempt.timed_out": "execution/attemptTimedOut",
        "execution.cancel.confirmed": "execution/cancelConfirmed",
        "execution.cancel.failed": "execution/cancelFailed",
        "improvement.shadow.started": "improvement/shadowStarted",
        "improvement.shadow.completed": "improvement/shadowCompleted",
        "improvement.canary.started": "improvement/canaryStarted",
        "improvement.canary.stopped": "improvement/canaryStopped",
        "improvement.promotion.applied": "improvement/promoted",
        "improvement.rollback.applied": "improvement/rolledBack",
        "export.release.confirmed": "export/released",
    }
    for internal_event, notification in events.items():
        assert notification in notifications_for_internal_event(internal_event)
        assert notification in IMPLEMENTED_NOTIFICATIONS
    assert notifications_for_internal_event("unknown.event") == ()
