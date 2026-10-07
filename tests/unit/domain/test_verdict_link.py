"""Verdict link: when a hypothesis stands on an older verdict, and what a re-check keeps."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from thoth.domain.verdict_link import (
    CurrentVerdict,
    LinkRecheckEvent,
    LinkState,
    VerdictLink,
    assess_link,
    is_flip,
    note_required,
    verdict_core_digest,
)
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
    verdict_digest_of,
)

H = "hypothesis:one"


def link(state: str = "HOLD_NO_RESULT", digest: str = "d1") -> VerdictLink:
    return VerdictLink(
        subject_kind="CRITERION",
        subject_id="C-1",
        verdict_revision="r" * 64,
        verdict_digest=digest,
        state=state,
    )


def now(state: str, digest: str, revision: str = "n" * 64) -> CurrentVerdict:
    return CurrentVerdict(verdict_revision=revision, verdict_digest=digest, state=state)


def event(
    reason: str,
    *,
    current: str = "d2",
    hypothesis: str = H,
    link_digest: str = "d1",
    flipped: bool = False,
) -> LinkRecheckEvent:
    return LinkRecheckEvent(
        event_id="e1",
        batch_id="b1",
        hypothesis_id=hypothesis,
        hypothesis_revision_digest="h" * 64,
        subject_kind="CRITERION",
        subject_id="C-1",
        link_verdict_digest=link_digest,
        link_state="HOLD_NO_RESULT",
        current_verdict_revision="n" * 64,
        current_verdict_digest=current,
        current_state="FAIL_COMPUTED",
        flipped=flipped,
        reason_code=reason,  # type: ignore[arg-type]
        note="",
        actor_id="human:local-user",
        created_at=datetime(2026, 10, 6, tzinfo=UTC),
    )


def test_a_hypothesis_without_a_link_is_not_judged() -> None:
    assert assess_link(None, now("PASS", "d9")).state is LinkState.NONE


def test_the_same_verdict_content_is_current_even_when_only_a_confirmation_was_added() -> None:
    # a confirmation is stored apart from the verdict: the digest of its content does not move
    result = assess_link(link(), now("HOLD_NO_RESULT", "d1", revision="m" * 64))
    assert result.state is LinkState.CURRENT and result.change is None


def test_a_changed_verdict_makes_the_link_stale_and_says_how() -> None:
    held_to_failed = assess_link(link(), now("FAIL_COMPUTED", "d2"))
    assert (held_to_failed.state, held_to_failed.change) == (LinkState.STALE, "CONTENT_CHANGED")
    assert (held_to_failed.link_state, held_to_failed.current_state) == (
        "HOLD_NO_RESULT",
        "FAIL_COMPUTED",
    )
    same_state_other_reason = assess_link(link("FAIL_COMPUTED"), now("FAIL_COMPUTED", "d2"))
    assert (
        same_state_other_reason.change == "CONTENT_CHANGED"
    )  # the state is the same; the content is not


@pytest.mark.parametrize(
    ("before", "after", "flip"),
    [
        ("PASS_COMPUTED", "FAIL_COMPUTED", True),
        ("FAIL_COMPUTED", "PASS_COMPUTED", True),
        ("PASS", "FAIL", True),
        ("FAIL", "PASS", True),
        ("PASS", "FAIL_WITH_INCOMPLETE_COVERAGE", True),
        ("HOLD_NO_RESULT", "FAIL_COMPUTED", False),
        ("PASS_COMPUTED", "HOLD_NO_RESULT", False),
        ("FAIL_COMPUTED", "FAIL_COMPUTED", False),
        ("PASS_COMPUTED", "PASS_COMPUTED", False),
    ],
)
def test_only_met_to_failed_and_back_is_a_flip(before: str, after: str, flip: bool) -> None:
    assert is_flip(before, after) is flip
    found = assess_link(link(before), now(after, "d2"))
    assert found.change == ("FLIPPED" if flip else "CONTENT_CHANGED")


def test_a_row_that_is_gone_is_stale() -> None:
    found = assess_link(link(), None)
    assert (found.state, found.change, found.current_state) == (
        LinkState.STALE,
        "SUBJECT_MISSING",
        None,
    )


def test_a_kept_recheck_of_this_very_change_makes_it_rechecked() -> None:
    for reason in ("UNRELATED", "STILL_MATCHES", "OTHER"):
        found = assess_link(link(), now("FAIL_COMPUTED", "d2"), (event(reason),), hypothesis_id=H)
        assert found.state is LinkState.RECHECKED, reason
        assert found.recheck is not None and found.recheck.reason_code == reason


def test_a_recheck_that_asks_for_more_research_leaves_it_stale_and_keeps_the_event() -> None:
    found = assess_link(
        link(), now("FAIL_COMPUTED", "d2"), (event("NEEDS_RESEARCH"),), hypothesis_id=H
    )
    assert found.state is LinkState.STALE and found.recheck is not None


def test_a_recheck_only_counts_for_the_hypothesis_and_the_change_it_looked_at() -> None:
    kept = event("STILL_MATCHES")
    assert (
        assess_link(
            link(), now("FAIL_COMPUTED", "d2"), (kept,), hypothesis_id="hypothesis:other"
        ).state
        is LinkState.STALE
    )
    assert (
        assess_link(link(), now("FAIL_COMPUTED", "d3"), (kept,), hypothesis_id=H).state
        is LinkState.STALE
    )  # it changed again
    assert (
        assess_link(link(digest="d0"), now("FAIL_COMPUTED", "d2"), (kept,), hypothesis_id=H).state
        is LinkState.STALE
    )  # another link
    # and when the verdict returns to what the hypothesis stood on, it is simply current
    assert (
        assess_link(link(), now("HOLD_NO_RESULT", "d1"), (kept,), hypothesis_id=H).state
        is LinkState.CURRENT
    )


def test_the_newest_event_for_a_change_wins() -> None:
    first, second = event("STILL_MATCHES"), event("NEEDS_RESEARCH")
    assert (
        assess_link(link(), now("FAIL_COMPUTED", "d2"), (first, second), hypothesis_id=H).state
        is LinkState.STALE
    )
    assert (
        assess_link(link(), now("FAIL_COMPUTED", "d2"), (second, first), hypothesis_id=H).state
        is LinkState.RECHECKED
    )


@pytest.mark.parametrize(
    ("reason", "flipped", "needed"),
    [
        ("UNRELATED", False, False),
        ("STILL_MATCHES", False, False),
        ("NEEDS_RESEARCH", False, False),
        ("UNRELATED", True, True),
        ("STILL_MATCHES", True, True),
        ("NEEDS_RESEARCH", True, False),
        ("OTHER", False, True),
        ("OTHER", True, True),
    ],
)
def test_free_text_is_needed_for_other_and_for_keeping_a_flipped_link(
    reason: str, flipped: bool, needed: bool
) -> None:
    assert note_required(reason, flipped) is needed


# --- a changed basis under the same verdict --------------------------------------------------


def draft(
    *,
    state: CriterionVerdictState = CriterionVerdictState.PASS_COMPUTED,
    chosen_revision: int = 1,
    source: str = "a.yaml#x",
    reasons: tuple[str, ...] = (),
    threshold: str = "0.90",
) -> VerdictDraft:
    chosen = ResultCandidate(result_id="RES-1", result_revision=chosen_revision)
    return VerdictDraft(
        subject_kind=SubjectKind.CRITERION,
        subject_id="C-1",
        state=state,
        cause=CauseOfChange(trigger="CSV_IMPORT"),
        basis=VerdictBasis(input_digest=f"in-{chosen_revision}", source_span_refs=(source,)),
        selection=ResultSelection(
            policy=SelectionPolicy(), candidates=(chosen,), chosen=(chosen,), excluded=()
        ),
        conditions=AppliedConditions(
            rule_id="R-1", rule_revision=1, condition="weather=dry", threshold=Decimal(threshold)
        ),
        actor=VerdictActor(computed_at=datetime(2026, 10, 6, tzinfo=UTC)),
        reasons=VerdictReasons(computed=reasons),
    )


def test_the_core_ignores_which_results_and_evidence_positions_stand_behind_the_verdict() -> None:
    base = draft()
    assert verdict_core_digest(draft(chosen_revision=2)) == verdict_core_digest(base)
    assert verdict_core_digest(draft(source="b.yaml#y")) == verdict_core_digest(base)
    # while the full content digest does move, so the link still goes stale
    assert verdict_digest_of(draft(chosen_revision=2)) != verdict_digest_of(base)


def test_the_core_moves_with_the_state_the_reasons_and_the_rule_that_applied() -> None:
    base = verdict_core_digest(draft())
    assert verdict_core_digest(draft(state=CriterionVerdictState.FAIL_COMPUTED)) != base
    assert verdict_core_digest(draft(reasons=("THRESHOLD_NOT_MET:RES-1:value=0.80",))) != base
    assert verdict_core_digest(draft(threshold="0.95")) != base


def test_the_same_state_and_reasons_with_other_results_is_told_apart_as_a_changed_basis() -> None:
    stored = link(state="PASS_COMPUTED", digest="d1")
    same_core = assess_link(
        stored,
        CurrentVerdict(
            verdict_revision="n" * 64, verdict_digest="d2", state="PASS_COMPUTED", core_digest="c1"
        ),
        link_core_digest="c1",
    )
    assert (same_core.state, same_core.change) == (LinkState.STALE, "EVIDENCE_ONLY")
    other_core = assess_link(
        stored,
        CurrentVerdict(
            verdict_revision="n" * 64, verdict_digest="d2", state="PASS_COMPUTED", core_digest="c2"
        ),
        link_core_digest="c1",
    )
    assert other_core.change == "CONTENT_CHANGED"


def test_a_link_made_before_the_core_was_known_is_not_called_a_changed_basis() -> None:
    current = CurrentVerdict(
        verdict_revision="n" * 64, verdict_digest="d2", state="PASS_COMPUTED", core_digest="c1"
    )
    unknown = assess_link(link(state="PASS_COMPUTED"), current)  # no core to compare with
    assert (unknown.state, unknown.change) == (LinkState.STALE, "CONTENT_CHANGED")


def test_a_changed_basis_stays_stale_and_a_recheck_of_it_is_kept_like_any_other() -> None:
    current = CurrentVerdict(
        verdict_revision="n" * 64, verdict_digest="d2", state="PASS_COMPUTED", core_digest="c1"
    )
    kept = event("STILL_MATCHES", current="d2")
    result = assess_link(
        link(state="PASS_COMPUTED"), current, (kept,), hypothesis_id=H, link_core_digest="c1"
    )
    assert (result.state, result.change) == (LinkState.RECHECKED, "EVIDENCE_ONLY")
    assert not note_required("UNRELATED", flipped=False)  # no flip, no words needed
