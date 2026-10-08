"""Currentness precedence and stable delta ordering through the original public imports."""

from copy import deepcopy
from typing import NoReturn, cast

import pytest

from thoth.application.services.research_followup_projection import (
    decision_delta,
    project_thread_followup,
)
from thoth.domain.research_followup import ResultIdentity
from thoth.domain.research_reference import RevisionRef
from thoth.domain.research_request import CurrentResultManifest, ThreadRequestRevision
from thoth.ports.ledger import LedgerPort
from thoth.ports.resource_scope import ResourceAccessPort


class NoRecordAccess:
    def __getattr__(self, name: str) -> NoReturn:
        raise AssertionError(f"empty record refs must not access {name}")


def request_ref() -> RevisionRef:
    return RevisionRef(
        project_id="p",
        entity_type="THREAD",
        entity_id="request:t",
        revision_id="revision:request",
        revision_digest="a" * 64,
    )


def request_record(ref: RevisionRef) -> ThreadRequestRevision:
    return ThreadRequestRevision(
        project_id="p",
        thread_id="t",
        request_epoch=1,
        accepted_input_ids=("input:1",),
        edit_kind="APPEND",
        authored_text="Question",
        authored_text_ref=ref,
        effective_question="Question",
        scope={},
        cutoff_at="2026-10-08T00:00:00Z",
        policy_ref="policy:p",
        policy_digest="b" * 64,
        actor_ref="human:local-user",
        operation_id="operation:1",
    )


@pytest.mark.parametrize(
    ("currentness", "operation", "phase", "state", "action"),
    [
        ("CURRENT", "SUCCEEDED", "COMPLETE", "COMPLETE", "NONE"),
        ("REVIEW_REQUIRED", "SUCCEEDED", "COMPLETE", "NEEDS_REVIEW", "REVIEW_CURRENTNESS"),
        ("INVALIDATED", "SUCCEEDED", "HOLD", "HOLD", "REVIEW_CURRENTNESS"),
        ("CURRENT", "FAILED", "HOLD", "FAILED", "OPEN_RESULT_DETAIL"),
        ("CURRENT", "CANCELLED", "COMPLETE", "CANCELLED", "OPEN_RESULT_DETAIL"),
        ("CURRENT", "RUNNING", None, "PENDING_OR_NOT_PRODUCED", "OPEN_RESULT_DETAIL"),
        ("UNAVAILABLE", "RUNNING", None, "PENDING_OR_NOT_PRODUCED", "OPEN_RESULT_DETAIL"),
    ],
)
def test_currentness_progress_and_action_precedence_without_record_io(
    currentness: str, operation: str, phase: str | None, state: str, action: str
) -> None:
    ref = request_ref()
    manifest = (
        None
        if phase is None
        else CurrentResultManifest(
            request_ref=ref,
            operation_id="operation:1",
            attempt_epoch=1,
            basis_digest="basis",
            phase=phase,
            completion="TERMINAL",
            input_ids=("input:1",),
            gaps=("gap:z", "gap:a", "gap:z"),
            next_steps=("gap:a", "next:1"),
        )
    )
    basis: dict[str, object] = {"state": currentness, "reasons": ["reason:z", "reason:a"]}
    before = deepcopy((manifest, basis))
    summary, matrix, next_action = project_thread_followup(
        ledger=cast(LedgerPort, NoRecordAccess()),
        access=cast(ResourceAccessPort, NoRecordAccess()),
        request_ref=ref,
        request=request_record(ref),
        result_ref=None,
        manifest=manifest,
        attempt=None,
        operation_state=operation,
        currentness_state=basis,
    )
    assert summary.state == state
    assert next_action.action_type == action
    assert summary.currentness.reasons == ("reason:z", "reason:a")
    assert matrix.availability == "UNAVAILABLE"
    assert matrix.reason_codes == ("REQUIREMENT_SET_UNAVAILABLE",)
    if manifest is not None:
        assert summary.remaining_gaps == ("gap:z", "gap:a", "next:1")
        assert summary.unknowns == (
            "LEGACY_BASIS_UNKNOWN",
            "REQUIREMENT_SET_UNAVAILABLE",
            "COVERAGE_UNAVAILABLE",
        )
    if action == "REVIEW_CURRENTNESS":
        assert next_action.reason_codes == ("reason:z", "reason:a")
    assert (manifest, basis) == before


def test_delta_group_and_reason_order_and_unavailable_state_are_preserved() -> None:
    before: dict[str, object] = {"phase": "OLD", "result": {"answer": "old", "other": 1}}
    after: dict[str, object] = {
        "phase": "NEW",
        "terminal_reason": "reason:t",
        "gaps": ["gap:z", "gap:a", "gap:z"],
        "next_steps": ["next:1", "gap:a"],
        "result": {
            "answer": "new",
            "hypotheses": ["changed"],
            "actions": ["check"],
            "other": 2,
            "coverage": {
                "reasons": ["reason:r", "gap:z"],
                "allowed_next_steps": ["next:1"],
                "conflicts": ["conflict:1"],
                "gates": {"target:z": "HOLD", "target:a": "HOLD"},
            },
        },
        "record_refs": [request_ref().model_dump(mode="json")],
    }
    identities = {
        "before": ResultIdentity(request_revision_digest="a" * 64, result_revision_digest="b" * 64),
        "after": ResultIdentity(request_revision_digest="c" * 64, result_revision_digest="d" * 64),
    }
    inputs = deepcopy((before, after))
    delta = decision_delta(
        project_id="p",
        thread_id="t",
        before=identities["before"],
        after=identities["after"],
        before_manifest=before,
        after_manifest=after,
        before_currentness={"state": "CURRENT"},
        after_currentness={"state": "REVIEW_REQUIRED"},
    )
    assert delta.state == "CHANGED"
    assert [group.kind for group in delta.groups] == [
        "ACTION",
        "CONDITION",
        "CONTENT",
        "EVIDENCE",
        "OTHER",
        "STATUS",
    ]
    assert delta.reason_codes == (
        "reason:t",
        "gap:z",
        "gap:a",
        "next:1",
        "reason:r",
        "conflict:1",
        "target:z",
        "target:a",
    )
    assert delta.reason_refs == (request_ref(),)
    assert delta.criteria == () and delta.criteria_state == "UNAVAILABLE"
    absent = decision_delta(
        project_id="p",
        thread_id="t",
        before=identities["before"],
        after=identities["after"],
        before_manifest=None,
        after_manifest=after,
        before_currentness={"state": "UNAVAILABLE"},
        after_currentness={"state": "CURRENT"},
    )
    assert absent.state == "UNAVAILABLE" and absent.groups == ()
    assert absent.reason_state == "UNKNOWN_REASON" and absent.reason_refs == ()
    assert (before, after) == inputs
