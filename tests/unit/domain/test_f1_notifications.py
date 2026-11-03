from __future__ import annotations

from typing import cast

import pytest
from pydantic import JsonValue

from thoth.protocol.notifications import IMPLEMENTED_NOTIFICATIONS, notifications_for

# Each call of the f1 catalog (project, thread, investigation, evidence) with the notifications it
# announces, in order. An entry that announced nothing, or the wrong thing, fails its own row.
F1_NOTIFICATIONS: tuple[tuple[str, dict[str, object], tuple[str, ...]], ...] = (
    ("project/create", {}, ("project/created",)),
    ("project/activate", {}, ("project/activated", "project/statusChanged")),
    ("project/archive", {}, ("project/archived", "project/statusChanged")),
    ("project/metadata/update", {}, ("project/metadata/updated",)),
    ("project/source/connect", {}, ("project/source/connected",)),
    (
        "project/source/disconnect",
        {"binding": {"state": "DETACHED"}},
        ("project/source/disconnected",),
    ),
    (
        "project/source/disconnect",
        {"binding": {"state": "REVOKED"}},
        (
            "project/source/revoked",
            "evidence/invalidated",
            "criteria/invalidated",
            "outcome/invalidated",
        ),
    ),
    ("project/role/assign", {}, ("project/role/assigned",)),
    ("project/role/revoke", {}, ("project/role/revoked",)),
    ("project/overlay/update", {}, ("project/overlay/updated",)),
    ("project/policy/update", {}, ("project/policy/updated",)),
    ("project/cutoff/update", {}, ("project/cutoff/updated",)),
    ("project/reference/import", {}, ("project/reference/imported",)),
    ("thread/start", {}, ("thread/started", "thread/cycleStarted", "thread/statusChanged")),
    ("thread/input", {"queued": True}, ("thread/inputQueued",)),
    (
        "thread/input",
        {
            "assessment": {"derived_status": ["EXPERT_INPUT_REQUIRED"]},
            "action_plan": {"alternatives": [{"execution_authority": "HUMAN_REQUIRED_R3"}]},
        },
        ("thread/inputAccepted", "thread/waitingForInput", "thread/waitingForProtectedAction"),
    ),
    ("thread/steer", {}, ("thread/steered",)),
    (
        "thread/pause",
        {"execution_state": "PAUSE_PENDING"},
        ("thread/pauseRequested", "thread/checkpointCreated", "thread/statusChanged"),
    ),
    (
        "thread/pause",
        {"execution_state": "PAUSED"},
        ("thread/paused", "thread/checkpointCreated", "thread/statusChanged"),
    ),
    ("thread/resume", {}, ("thread/resumed", "thread/statusChanged")),
    (
        "thread/stop",
        {},
        (
            "thread/stopRequested",
            "thread/stopped",
            "thread/cycleTerminated",
            "thread/statusChanged",
            "thread/checkpointCreated",
        ),
    ),
    ("thread/fork", {}, ("thread/forked",)),
    ("thread/metadata/update", {}, ("thread/metadataUpdated",)),
    (
        "investigation/start",
        {"investigation": {"execution_state": "IDLE"}},
        ("investigation/started", "investigation/statusChanged"),
    ),
    (
        "investigation/update",
        {"investigation": {"execution_state": "WAITING_INPUT"}},
        (
            "investigation/progress",
            "investigation/sufficiencyUpdated",
            "investigation/statusChanged",
            "investigation/waitingForInput",
        ),
    ),
    ("investigation/pause", {}, ("investigation/checkpointCreated", "investigation/statusChanged")),
    (
        "investigation/resume",
        {"investigation": {"execution_state": "IDLE"}},
        ("investigation/statusChanged",),
    ),
    ("investigation/stop", {}, ("investigation/completed", "investigation/statusChanged")),
    ("evidence/source/add", {}, ("evidence/sourceUpdated",)),
    ("evidence/source/refresh", {}, ("evidence/sourceUpdated",)),
    ("evidence/source/metadata/correct", {}, ("evidence/sourceUpdated",)),
    ("evidence/span/correct", {}, ("evidence/updated",)),
    ("evidence/link/propose", {}, ("evidence/updated",)),
    ("evidence/link/correct", {}, ("evidence/updated",)),
    ("evidence/challenge", {}, ("evidence/conflictUpdated",)),
    ("evidence/revalidate", {}, ("evidence/revalidated",)),
)


@pytest.mark.parametrize(("method", "result", "expected"), F1_NOTIFICATIONS)
def test_each_f1_call_announces_its_own_notifications(
    method: str, result: dict[str, object], expected: tuple[str, ...]
) -> None:
    assert expected
    assert notifications_for(method, cast(dict[str, JsonValue], result)) == expected
    assert set(expected) <= set(IMPLEMENTED_NOTIFICATIONS)
