from __future__ import annotations

from typing import cast

from pydantic import JsonValue

from thoth.protocol.notifications import IMPLEMENTED_NOTIFICATIONS, notifications_for


def test_receipt_closure_export_notification_contracts() -> None:
    emitted: set[str] = set()
    for method in (
        "receipt/seal",
        "receipt/bundle/create",
        "receipt/correction/create",
        "closure/prepare",
        "closure/decide",
        "closure/followup/create",
        "closure/reopen",
        "closure/retention/plan",
        "closure/purge/prepare",
        "export/plan/create",
        "export/snapshot/create",
        "export/generate",
        "export/release/prepare",
        "export/correction/create",
    ):
        emitted.update(notifications_for(method, cast(dict[str, JsonValue], {})))
    emitted.update(
        notifications_for(
            "receipt/verify",
            cast(dict[str, JsonValue], {"verification": {"state": "VERIFIED"}}),
        )
    )
    emitted.update(
        notifications_for(
            "receipt/verify",
            cast(dict[str, JsonValue], {"verification": {"state": "FAILED"}}),
        )
    )
    emitted.update(
        notifications_for(
            "closure/readiness/assess",
            cast(dict[str, JsonValue], {"blocked": True}),
        )
    )
    emitted.update(
        notifications_for(
            "export/verify",
            cast(
                dict[str, JsonValue],
                {
                    "verification": {
                        "state": "VERIFIED",
                        "payload": {"release_eligible": True},
                    }
                },
            ),
        )
    )
    emitted.update(
        notifications_for(
            "export/verify",
            cast(dict[str, JsonValue], {"verification": {"state": "REJECTED"}}),
        )
    )
    expected = {
        "receipt/sealing",
        "receipt/sealed",
        "receipt/verified",
        "receipt/verificationFailed",
        "receipt/bundleCreated",
        "receipt/corrected",
        "closure/readinessChanged",
        "closure/prepared",
        "closure/decided",
        "closure/blocked",
        "closure/followupCreated",
        "closure/reopened",
        "closure/retentionChanged",
        "closure/purgePrepared",
        "export/planned",
        "export/snapshotSealed",
        "export/generating",
        "export/generated",
        "export/verified",
        "export/verificationFailed",
        "export/releaseEligible",
        "export/releasePrepared",
        "export/corrected",
        "export/superseded",
    }
    assert expected <= emitted
    assert expected <= IMPLEMENTED_NOTIFICATIONS
