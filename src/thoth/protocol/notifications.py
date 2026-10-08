from __future__ import annotations

from typing import cast

from pydantic import JsonValue

from thoth.protocol.notification_catalog import (
    IMPLEMENTED_NOTIFICATIONS as IMPLEMENTED_NOTIFICATIONS,
)
from thoth.protocol.notification_catalog import (
    INTERNAL_NOTIFICATION_EVENTS,
)
from thoth.protocol.notification_catalog import (
    STATIC_NOTIFICATIONS as STATIC_NOTIFICATIONS,
)
from thoth.protocol.notification_rules import (
    append_decision_notifications,
    append_lifecycle_notifications,
    append_research_notifications,
    append_revision_learning_notifications,
)


def notifications_for(method: str, result: dict[str, JsonValue]) -> tuple[str, ...]:
    values = list(STATIC_NOTIFICATIONS.get(method, ()))
    append_research_notifications(method, result, values)
    append_decision_notifications(method, result, values)
    append_revision_learning_notifications(method, result, values)
    append_lifecycle_notifications(method, result, values)
    if method in {"execution/start", "execution/resume", "execution/reconcile"}:
        attempts = result.get("attempts") or result.get("new_attempts")
        if isinstance(attempts, list) and any(
            isinstance(item, dict)
            and cast(dict[object, object], item).get("authorization_digest") is not None
            for item in attempts
        ):
            values.append("action/authorizationConsumed")
    return tuple(dict.fromkeys(values))


def notifications_for_internal_event(event_type: str) -> tuple[str, ...]:
    """Map authenticated worker/deployment callbacks to public notifications."""
    return INTERNAL_NOTIFICATION_EVENTS.get(event_type, ())
