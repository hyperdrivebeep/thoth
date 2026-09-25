from __future__ import annotations

from typing import cast

from pydantic import JsonValue

from thoth.protocol.notifications import IMPLEMENTED_NOTIFICATIONS, notifications_for


def test_outcome_notification_mapping_covers_f4_contract() -> None:
    emitted: set[str] = set()
    for method in (
        "outcome/series/create",
        "outcome/assess",
        "outcome/reassess",
        "outcome/attribution/assess",
        "outcome/changeSet/propose",
        "outcome/followup/generate",
        "outcome/impact/propose",
    ):
        emitted.update(notifications_for(method, cast(dict[str, JsonValue], {})))
    emitted.update(
        notifications_for(
            "outcome/observation/link",
            cast(
                dict[str, JsonValue],
                {
                    "series": {
                        "phase_states": {"INTERIM": "READY_TO_ASSESS"},
                        "assessment_refs": ["outcome:1"],
                    }
                },
            ),
        )
    )
    emitted.update(
        notifications_for(
            "project/source/disconnect",
            cast(dict[str, JsonValue], {"binding": {"state": "REVOKED"}}),
        )
    )
    expected = {
        "outcome/seriesCreated",
        "outcome/observationLinked",
        "outcome/readyToAssess",
        "outcome/assessed",
        "outcome/reassessmentRequired",
        "outcome/reassessed",
        "outcome/attributionUpdated",
        "outcome/changeSetProposed",
        "outcome/followupGenerated",
        "outcome/impactProposed",
        "outcome/invalidated",
        "outcome/superseded",
    }
    assert expected <= emitted
    assert expected <= IMPLEMENTED_NOTIFICATIONS
