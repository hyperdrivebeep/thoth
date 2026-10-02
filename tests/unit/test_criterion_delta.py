"""Criteria of two results pair only when id, kind, target and question all match."""

from __future__ import annotations

from thoth.application.services.research_criterion_delta import CriterionView, pair_criteria
from thoth.domain.evidence_requirements import EvidenceRequirement
from thoth.domain.research_followup import CoverageMatrixRow


def view(
    requirement_id: str,
    *,
    question: str = "자료가 현재인가",
    target: str = "RESEARCH_GAP",
    kind: str = "RESEARCH_CHECK",
    status: str = "UNRESOLVED",
    relation: str = "QUALIFIES",
    validation: str = "INCONCLUSIVE",
    blocker: str = "RESEARCH_GAP",
    refs: tuple[str, ...] = (),
) -> CriterionView:
    requirement = EvidenceRequirement.model_validate(
        {
            "requirement_id": requirement_id,
            "kind": kind,
            "target": target,
            "question": question,
            "rationale": "r",
            "needed_for": "n",
            "blocker": blocker,
            "followup": "f",
        }
    )
    row = CoverageMatrixRow.model_validate(
        {
            "requirement_id": requirement_id,
            "target": target,
            "question": question,
            "status": status,
            "applicability": "APPLICABLE",
            "relation": relation,
            "validation": validation,
            "blocker": blocker,
            "evidence_refs": refs,
        }
    )
    return CriterionView(requirement=requirement, row=row)


def test_the_same_criterion_shows_what_changed_between_two_results() -> None:
    (delta,) = pair_criteria(
        (view("check:0", refs=("span:a",)),),
        (
            view(
                "check:0",
                status="SATISFIED",
                relation="SUPPORTS",
                validation="APPLIED",
                blocker="",
                refs=("span:a", "span:b"),
            ),
        ),
    )
    assert delta.match == "SAME" and delta.unchanged_hold is False
    assert delta.before is not None and delta.before.status == "UNRESOLVED"
    assert delta.after is not None and delta.after.status == "SATISFIED"
    assert (delta.before.blocker, delta.after.blocker) == ("RESEARCH_GAP", "")
    assert delta.evidence_refs_added == ("span:b",) and delta.evidence_refs_removed == ()


def test_a_criterion_that_did_not_change_and_is_still_open_is_an_unchanged_hold() -> None:
    (delta,) = pair_criteria((view("check:1"),), (view("check:1"),))
    assert delta.match == "SAME" and delta.unchanged_hold is True
    assert delta.evidence_refs_added == () and delta.evidence_refs_removed == ()


def test_a_fulfilled_criterion_that_stayed_fulfilled_is_not_a_hold() -> None:
    fulfilled = view(
        "check:2", status="SATISFIED", relation="SUPPORTS", validation="APPLIED", blocker=""
    )
    (delta,) = pair_criteria((fulfilled,), (fulfilled,))
    assert delta.match == "SAME" and delta.unchanged_hold is False


def test_a_changed_blocker_alone_is_a_change_not_a_hold() -> None:
    (delta,) = pair_criteria((view("check:3"),), (view("check:3", blocker="answer:HOLD"),))
    assert delta.unchanged_hold is False
    assert delta.before is not None and delta.after is not None
    assert (delta.before.blocker, delta.after.blocker) == ("RESEARCH_GAP", "answer:HOLD")


def test_a_check_number_that_now_means_another_question_is_added_and_removed_not_paired() -> None:
    deltas = pair_criteria(
        (view("check:0", question="센서 조합이 명시되는가"),),
        (view("check:0", question="적용 문서 버전이 현재인가"),),
    )
    assert [(d.match, d.question) for d in deltas] == [
        ("ADDED", "적용 문서 버전이 현재인가"),
        ("REMOVED", "센서 조합이 명시되는가"),
    ]
    added, removed = deltas
    assert added.before is None and added.after is not None
    assert removed.after is None and removed.before is not None


def test_target_or_kind_differences_also_prevent_pairing() -> None:
    assert [
        d.match for d in pair_criteria((view("bound:0"),), (view("bound:0", target="time"),))
    ] == [
        "ADDED",
        "REMOVED",
    ]
    assert [
        d.match
        for d in pair_criteria((view("bound:0"),), (view("bound:0", kind="BOUND_OBLIGATION"),))
    ] == ["ADDED", "REMOVED"]


def test_criteria_only_in_one_result_are_added_or_removed() -> None:
    deltas = pair_criteria(
        (view("check:0"), view("check:1", question="옛 질문")),
        (view("check:0"), view("check:2", question="새 질문")),
    )
    assert [(d.match, d.requirement_id) for d in deltas] == [
        ("SAME", "check:0"),
        ("ADDED", "check:2"),
        ("REMOVED", "check:1"),
    ]
