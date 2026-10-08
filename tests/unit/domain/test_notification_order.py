"""Exact event order, cross-namespace additions and final deduplication are contracts."""

from copy import deepcopy

import pytest
from pydantic import JsonValue

from thoth.protocol.notifications import (
    STATIC_NOTIFICATIONS,
    notifications_for,
    notifications_for_internal_event,
)

CASES: tuple[tuple[str, dict[str, JsonValue], tuple[str, ...]], ...] = (
    (
        "revision/changeSet/commit",
        {
            "atomic_commit": True,
            "contains_restore": True,
            "domain_transitions": [
                "action/retired",
                "revision/headChanged",
                "memory/committed",
                "action/retired",
                42,
            ],
        },
        (
            "revision/changeSetCommitted",
            "revision/headChanged",
            "revision/projectionStale",
            "revision/invalidated",
            "revision/restored",
            "action/retired",
            "memory/committed",
            "42",
        ),
    ),
    (
        "revision/changeSet/commit",
        {
            "atomic_commit": False,
            "contains_restore": True,
            "domain_transitions": ["action/retired"],
        },
        ("revision/changeSetAborted",),
    ),
    (
        "execution/reconcile",
        {
            "attempt": {"state": "SUCCEEDED"},
            "execution": {"state": "COMPLETED"},
            "attempts": [{"authorization_digest": ""}],
        },
        (
            "execution/reconciliationCompleted",
            "execution/frontierChanged",
            "execution/effectChanged",
            "execution/attemptSucceeded",
            "execution/planCompleted",
            "action/authorizationConsumed",
        ),
    ),
    (
        "execution/resume",
        {"attempts": [], "new_attempts": [{"authorization_digest": "bound"}]},
        (
            "execution/resumed",
            "execution/frontierChanged",
            "execution/stepDispatched",
            "action/authorizationConsumed",
        ),
    ),
    (
        "execution/resume",
        {
            "attempts": [{"authorization_digest": None}],
            "new_attempts": [{"authorization_digest": "bound"}],
        },
        ("execution/resumed", "execution/frontierChanged", "execution/stepDispatched"),
    ),
    (
        "outcome/observation/link",
        {"series": {"phase_states": {"INTERIM": "READY_TO_ASSESS"}, "assessment_refs": ["old"]}},
        ("outcome/observationLinked", "outcome/readyToAssess", "outcome/reassessmentRequired"),
    ),
    (
        "thread/input",
        {
            "queued": True,
            "assessment": {"derived_status": ["EXPERT_INPUT_REQUIRED"]},
            "action_plan": {"alternatives": [{"execution_authority": "HUMAN_REQUIRED_R3"}]},
        },
        ("thread/inputQueued",),
    ),
    (
        "thread/input",
        {
            "assessment": {"derived_status": ["EXPERT_INPUT_REQUIRED"]},
            "action_plan": {"alternatives": [{"execution_authority": "HUMAN_REQUIRED_R3"}]},
        },
        ("thread/inputAccepted", "thread/waitingForInput", "thread/waitingForProtectedAction"),
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
    (
        "investigation/update",
        {"investigation": {"execution_state": "WAITING_BUDGET"}},
        (
            "investigation/progress",
            "investigation/sufficiencyUpdated",
            "investigation/statusChanged",
            "investigation/waitingForInput",
        ),
    ),
    (
        "improvement/evaluation/assess",
        {"evaluation": {"payload": {"critical_guardrail_failure": True}}},
        ("improvement/evaluated", "improvement/guardrailFailed"),
    ),
    (
        "improvement/exposure/prepare",
        {"exposure": {"payload": {"requested_exposure_state": "CANARY"}}},
        ("improvement/exposurePrepared", "improvement/canaryPrepared"),
    ),
    (
        "export/verify",
        {"verification": {"state": "FAILED", "payload": {"release_eligible": True}}},
        ("export/verificationFailed", "export/releaseEligible"),
    ),
    (
        "closure/readiness/assess",
        {"blocked": True},
        ("closure/readinessChanged", "closure/blocked"),
    ),
    ("receipt/verify", {"verification": {"state": "VERIFIED"}}, ("receipt/verified",)),
    ("project/activate", {}, ("project/activated", "project/statusChanged")),
    ("unknown/method", {}, ()),
)


@pytest.mark.parametrize(("method", "result", "expected"), CASES)
def test_notification_order_and_input_are_preserved(
    method: str, result: dict[str, JsonValue], expected: tuple[str, ...]
) -> None:
    before = deepcopy(result)
    assert notifications_for(method, result) == expected
    assert result == before


def test_static_and_dynamic_duplicates_keep_their_first_position(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(
        STATIC_NOTIFICATIONS,
        "revision/changeSet/commit",
        ("memory/committed", "revision/headChanged", "memory/committed"),
    )
    assert notifications_for(
        "revision/changeSet/commit",
        {"atomic_commit": True, "domain_transitions": ["action/retired", "memory/committed"]},
    ) == (
        "memory/committed",
        "revision/headChanged",
        "revision/changeSetCommitted",
        "revision/projectionStale",
        "revision/invalidated",
        "action/retired",
    )


def test_public_function_paths_and_internal_callback_results_are_preserved() -> None:
    assert notifications_for.__module__ == "thoth.protocol.notifications"
    assert notifications_for_internal_event.__module__ == "thoth.protocol.notifications"
    assert notifications_for_internal_event("execution.cancel.failed") == (
        "execution/cancelFailed",
    )
    assert notifications_for_internal_event("improvement.rollback.applied") == (
        "improvement/rolledBack",
    )
    assert notifications_for_internal_event("unknown.event") == ()
