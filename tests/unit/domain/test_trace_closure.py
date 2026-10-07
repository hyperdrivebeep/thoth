"""Closing a trace row: what a person records, and the one closure the rules compute.

"Effect confirmed" is computed: after a fix was recorded for a row that was not met, a later
verdict of that row is met and stands on results the earlier one did not. The other closures are
what a person decided outside the system; they are only recorded and never change a verdict.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from thoth.domain.trace_closure import ClosureEvent, effect_confirmed_by
from thoth.domain.verification_trace import (
    AppliedConditions,
    CauseOfChange,
    CriterionVerdictState,
    ResultCandidate,
    ResultSelection,
    SelectionPolicy,
    SubjectKind,
    VerdictActor,
    VerdictBasis,
    VerdictDraft,
    VerdictReasons,
    VerdictRevision,
    seal_verdict,
)


def revision(
    state: CriterionVerdictState, *results: int, parent: str | None = None
) -> VerdictRevision:
    chosen = tuple(ResultCandidate(result_id="RES-1", result_revision=n) for n in results)
    draft = VerdictDraft(
        subject_kind=SubjectKind.CRITERION,
        subject_id="C-1",
        state=state,
        cause=CauseOfChange(trigger="CSV_IMPORT"),
        basis=VerdictBasis(input_digest="in-" + "-".join(map(str, results))),
        selection=ResultSelection(policy=SelectionPolicy(), candidates=chosen, chosen=chosen),
        conditions=AppliedConditions(rule_id="R-1", rule_revision=1),
        actor=VerdictActor(computed_at=datetime(2026, 10, 6, tzinfo=UTC)),
        reasons=VerdictReasons(),
    )
    return seal_verdict(draft, parent)


FAIL, PASS = CriterionVerdictState.FAIL_COMPUTED, CriterionVerdictState.PASS_COMPUTED


def test_a_met_verdict_on_new_results_after_the_fix_confirms_the_effect() -> None:
    before = revision(FAIL, 1)
    assert effect_confirmed_by(before, [revision(PASS, 2)]) is not None


def test_a_met_verdict_on_the_same_results_is_not_an_effect() -> None:
    before = revision(FAIL, 1)
    assert effect_confirmed_by(before, [revision(PASS, 1)]) is None


def test_a_verdict_that_is_still_not_met_is_not_an_effect_however_the_value_moved() -> None:
    before = revision(FAIL, 1)
    held = revision(CriterionVerdictState.HOLD_INVALID_RESULT, 2)
    assert effect_confirmed_by(before, [held, revision(FAIL, 3)]) is None


def test_a_row_that_was_already_met_when_the_fix_was_recorded_has_no_effect_to_confirm() -> None:
    assert effect_confirmed_by(revision(PASS, 1), [revision(PASS, 2)]) is None


def test_no_later_verdict_means_nothing_is_confirmed() -> None:
    assert effect_confirmed_by(revision(FAIL, 1), []) is None


def event(**patch: object) -> dict[str, object]:
    base: dict[str, object] = {
        "event_id": "c1",
        "subject_kind": "CRITERION",
        "subject_id": "C-1",
        "kind": "FIX_APPLIED",
        "basis_ref": "ECN-12",
        "verdict_revision": "r" * 64,
        "verdict_digest": "d1",
        "verdict_state": "FAIL_COMPUTED",
        "actor_id": "human:local-user",
        "created_at": datetime(2026, 10, 6, tzinfo=UTC),
    }
    return {**base, **patch}


def test_a_closure_names_the_document_it_rests_on_and_only_the_four_recorded_kinds_exist() -> None:
    assert ClosureEvent(**event()).basis_ref == "ECN-12"  # type: ignore[arg-type]
    for bad in ({"basis_ref": ""}, {"kind": "EFFECT_CONFIRMED"}, {"kind": "WAIVED"}):
        with pytest.raises(ValidationError):
            ClosureEvent(**event(**bad))  # type: ignore[arg-type]
