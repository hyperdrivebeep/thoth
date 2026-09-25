from __future__ import annotations

from typing import cast

from pydantic import JsonValue

from thoth.protocol.notifications import IMPLEMENTED_NOTIFICATIONS, notifications_for


def test_project_and_thread_notification_mapping_covers_f1_catalog() -> None:
    emitted: set[str] = set()
    fixtures: tuple[tuple[str, object], ...] = (
        ("project/create", {}),
        ("project/activate", {}),
        ("project/archive", {}),
        ("project/metadata/update", {}),
        ("project/source/connect", {}),
        ("project/source/disconnect", {"binding": {"state": "DETACHED"}}),
        ("project/source/disconnect", {"binding": {"state": "REVOKED"}}),
        ("project/role/assign", {}),
        ("project/role/revoke", {}),
        ("project/overlay/update", {}),
        ("project/policy/update", {}),
        ("project/cutoff/update", {}),
        ("project/reference/import", {}),
        ("thread/start", {}),
        ("thread/input", {"queued": True}),
        (
            "thread/input",
            {
                "assessment": {"derived_status": ["EXPERT_INPUT_REQUIRED"]},
                "action_plan": {"alternatives": [{"execution_authority": "HUMAN_REQUIRED_R3"}]},
            },
        ),
        ("thread/steer", {}),
        ("thread/pause", {"execution_state": "PAUSE_PENDING"}),
        ("thread/pause", {"execution_state": "PAUSED"}),
        ("thread/resume", {}),
        ("thread/stop", {}),
        ("thread/fork", {}),
        ("thread/metadata/update", {}),
        ("investigation/start", {"investigation": {"execution_state": "IDLE"}}),
        (
            "investigation/update",
            {"investigation": {"execution_state": "WAITING_INPUT"}},
        ),
        ("investigation/pause", {}),
        ("investigation/resume", {"investigation": {"execution_state": "IDLE"}}),
        ("investigation/stop", {}),
        ("evidence/source/add", {}),
        ("evidence/source/refresh", {}),
        ("evidence/source/metadata/correct", {}),
        ("evidence/span/correct", {}),
        ("evidence/link/propose", {}),
        ("evidence/link/correct", {}),
        ("evidence/challenge", {}),
        ("evidence/revalidate", {}),
    )
    for method, result in fixtures:
        assert isinstance(result, dict)
        emitted.update(notifications_for(method, cast(dict[str, JsonValue], result)))

    assert emitted <= set(IMPLEMENTED_NOTIFICATIONS)
    assert emitted
