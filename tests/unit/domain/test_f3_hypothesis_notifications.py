from __future__ import annotations

from typing import cast

from pydantic import JsonValue

from thoth.protocol.notifications import IMPLEMENTED_NOTIFICATIONS, notifications_for


def test_hypothesis_notification_mapping_covers_active_f3_contract() -> None:
    methods = (
        "hypothesis/generate",
        "hypothesis/create",
        "hypothesis/revise",
        "hypothesis/intent/update",
        "hypothesis/causal/update",
        "hypothesis/relation/add",
        "hypothesis/relation/remove",
        "hypothesis/assumption/add",
        "hypothesis/prediction/bind",
        "hypothesis/counterevidence/request",
        "hypothesis/portfolio/compose",
        "hypothesis/portfolio/revalidate",
        "hypothesis/test/bind",
        "hypothesis/appraise",
        "hypothesis/split/propose",
        "hypothesis/merge/propose",
    )
    emitted: set[str] = set()
    for method in methods:
        emitted.update(notifications_for(method, cast(dict[str, JsonValue], {})))
    expected = {
        "hypothesis/generated",
        "hypothesis/created",
        "hypothesis/updated",
        "hypothesis/intentChanged",
        "hypothesis/stageChanged",
        "hypothesis/relationChanged",
        "hypothesis/assumptionChanged",
        "hypothesis/predictionBound",
        "hypothesis/counterevidenceRequested",
        "hypothesis/portfolioUpdated",
        "hypothesis/qualityUpdated",
        "hypothesis/testBound",
        "hypothesis/appraisalUpdated",
        "hypothesis/invalidated",
        "hypothesis/splitProposed",
        "hypothesis/mergeProposed",
    }
    assert expected <= emitted
    assert expected <= IMPLEMENTED_NOTIFICATIONS
