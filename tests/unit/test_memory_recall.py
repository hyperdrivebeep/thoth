"""A question is given a few memories inside fixed limits, in an order that uses no score."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from thoth.application.services.memory_recall import (
    AUTO_MEMORY_NO_EVIDENCE,
    AUTO_MEMORY_WEAK_MATCH,
    FOLLOW_UP_MARKERS,
    RecallLimits,
    common_terms,
    estimate_tokens,
    follow_up_markers,
    lacks_evidence,
    narrow_recall,
    order_recall,
    plan_recall,
)
from thoth.domain.enums import MemoryKind, MemoryPayloadMode
from thoth.domain.memory import (
    FullMemoryRevision,
    MemoryReviewRole,
    MemoryReviewVerdict,
    MemoryRoleReview,
    MemoryTransition,
)

NOW = datetime(2026, 9, 30, tzinfo=UTC)
REVIEWS = tuple(
    MemoryRoleReview(
        role=role, verdict=MemoryReviewVerdict.PASS, reason_code="OK", basis_digest="b" * 64
    )
    for role in MemoryReviewRole
)


def memory(
    tag: str,
    *,
    kind: MemoryKind = MemoryKind.HYPOTHESIS,
    source: str | None = None,
    body: str = "짧은 기억 한 줄",
    terms: tuple[str, ...] = ("표본",),
    minutes: int = 0,
    mode: MemoryPayloadMode = MemoryPayloadMode.DOMAIN_REFERENCE,
    evidence: tuple[str, ...] = (),
) -> FullMemoryRevision:
    return FullMemoryRevision(
        memory_revision_id=f"rev:{tag}",
        memory_id=f"memory:{tag}",
        project_id="p",
        origin_thread_id="thread:1",
        payload_mode=mode,
        kind=kind,
        owner_revision_ref="a" * 64,
        source_ref=source if source is not None else f"{kind.value}:{tag}",
        content_excerpt=f"{kind.value}:{tag}\n{body}",
        scope={},
        evidence_refs=evidence,
        query_terms=terms,
        support_status="SUPPORTED",
        authority_status="AUTHORITATIVE",
        cutoff_at=NOW,
        cutoff_valid=True,
        reviews=REVIEWS,
        transition=MemoryTransition.COMMIT,
        recall_eligible=True,
        action_eligible=False,
        revision_digest=(tag.encode().hex() * 64)[:64],
        created_at=NOW + timedelta(minutes=minutes),
    )


def ids(items: list[FullMemoryRevision]) -> list[str]:
    return [item.memory_id.removeprefix("memory:") for item in items]


def test_twenty_memories_fit_inside_every_limit_and_the_rest_are_counted() -> None:
    kinds = (MemoryKind.HYPOTHESIS, MemoryKind.ACTION, MemoryKind.FACT, MemoryKind.LESSON)
    items = [memory(f"m{n:02d}", kind=kinds[n % 4], source=f"REC:{n // 2}") for n in range(20)]
    plan = plan_recall(items, "표본", frozenset({"표본"}), RecallLimits())
    assert len(plan.included) <= 8 and len(plan.selected) <= 8
    assert plan.tokens <= 1_600
    per_kind: dict[str, int] = {}
    per_key: dict[str, int] = {}
    for item in plan.selected:
        per_kind[item.kind.value] = per_kind.get(item.kind.value, 0) + 1
        per_key[item.source_ref or ""] = per_key.get(item.source_ref or "", 0) + 1
    assert max(per_kind.values()) <= 3 and max(per_key.values()) <= 2
    assert len(plan.selected) + len(plan.omitted) == 20
    assert sum(plan.omitted_by_limit.values()) == len(plan.omitted)


def test_only_twenty_four_are_retrieved_and_one_kind_cannot_take_everything() -> None:
    items = [memory(f"h{n:02d}", source=f"HYP:{n}") for n in range(30)]
    plan = plan_recall(items, "표본", frozenset({"표본"}), RecallLimits())
    assert len(plan.retrieved) == 24 and plan.omitted_by_limit["candidates"] == 6
    assert len(plan.selected) == 3 and plan.omitted_by_limit["per_kind"] == 21


def test_an_item_is_cut_to_its_share_and_the_total_stops_at_the_budget() -> None:
    long_body = "가" * 3_000
    items = [
        memory(f"x{n}", kind=kind, body=long_body, source=f"K:{n}")
        for n, kind in enumerate(
            (MemoryKind.HYPOTHESIS, MemoryKind.ACTION, MemoryKind.FACT, MemoryKind.LESSON)
        )
    ]
    plan = plan_recall(items, "표본", frozenset({"표본"}), RecallLimits())
    assert all(estimate_tokens(item.content_excerpt) <= 320 for item in plan.included)
    assert len(plan.truncated) == len(plan.included) == 4
    assert plan.tokens <= 1_600
    tight = plan_recall(items, "표본", frozenset({"표본"}), RecallLimits(total_tokens=700))
    assert len(tight.included) == 2 and tight.omitted_by_limit == {"total_tokens": 2}


def test_the_order_is_id_then_phrase_then_word_hits_then_spread_then_newest_then_id() -> None:
    named = memory("named", source="HYPOTHESIS:hypothesis:o:special-one", terms=("가나다",))
    phrase = memory("phrase", body="표본이 작으면 결론을 보류한다", terms=("표본이",))
    many = memory("many", terms=("표본이", "결론을", "보류한다"))
    few = memory("few", terms=("표본이",))
    query = (
        "hypothesis:o:special-one 은 어떤가. 표본이 작으면 결론을 보류한다 "
        "를 표본이 결론을 보류한다"
    )
    terms = frozenset({"표본이", "결론을", "보류한다"})
    order = order_recall([few, many, phrase, named], query, terms)
    assert ids(order) == ["named", "phrase", "many", "few"]
    # equal on the first three: kinds alternate, then newest first, then id
    a1 = memory("a1", kind=MemoryKind.ACTION, minutes=1)
    a2 = memory("a2", kind=MemoryKind.ACTION, minutes=5)
    h1 = memory("h1", kind=MemoryKind.HYPOTHESIS, minutes=2)
    h2 = memory("h2", kind=MemoryKind.HYPOTHESIS, minutes=2)
    assert ids(order_recall([a1, h2, a2, h1], "표본", frozenset({"표본"}))) == [
        "a2",
        "h1",
        "a1",
        "h2",
    ]


def correction(tag: str, terms: tuple[str, ...], minutes: int = 0) -> FullMemoryRevision:
    """A user's correction: free text under MEMORY_ASSERTION, never an automatic reference."""
    return memory(
        tag,
        kind=MemoryKind.LESSON,
        source=f"MEMORY:{tag}",
        terms=terms,
        minutes=minutes,
        mode=MemoryPayloadMode.MEMORY_ASSERTION,
    ).model_copy(update={"assertion": "표본이 작으면 다시 확인한다"})


def pool(common: str = "프로젝트") -> list[FullMemoryRevision]:
    """Six automatic memories that all mention one project-wide word, each with its own words."""
    own = ("가설A", "가설B", "행동C", "행동D", "결과E", "결과F")
    return [
        memory(f"p{n}", terms=(common, own[n]), source=f"HYP:p{n}", evidence=("span:1",))
        for n in range(6)
    ]


def test_a_word_every_memory_shares_is_a_common_word_and_nothing_else_is() -> None:
    items = pool()
    assert common_terms(items) == frozenset({"프로젝트"})
    # a pool too small to tell what is common says no word is
    assert common_terms(items[:3]) == frozenset()


def test_an_automatic_memory_that_meets_the_question_on_a_common_word_only_is_left_out() -> None:
    items = pool()
    terms = frozenset({"프로젝트", "무관한"})
    narrowed = narrow_recall(items, "프로젝트 무관한 질문", terms)
    assert narrowed.relevant == []
    assert narrowed.excluded[AUTO_MEMORY_WEAK_MATCH] == [i.memory_revision_id for i in items]
    assert narrowed.common_terms == frozenset({"프로젝트"})
    # one word of its own is still only one word
    one = narrow_recall(items, "가설A 이야기", frozenset({"가설a", "가설A"}))
    assert [i.memory_id for i in one.relevant] == []


def test_two_words_that_are_not_common_let_an_automatic_memory_in() -> None:
    items = pool()
    terms = frozenset({"가설A", "가설B", "프로젝트"})
    narrowed = narrow_recall(items, "프로젝트 가설A 가설B", terms)
    # p0 meets 가설A only, p1 meets 가설B only: no memory has two of its own words
    assert narrowed.relevant == []
    both = memory("both", terms=("프로젝트", "가설A", "가설B"), evidence=("span:1",))
    narrowed = narrow_recall([*items, both], "프로젝트 가설A 가설B", terms)
    assert ids(narrowed.relevant) == ["both"]


def test_a_memory_that_does_not_meet_the_question_at_all_keeps_the_old_reason() -> None:
    narrowed = narrow_recall(pool(), "전혀 다른 말", frozenset({"전혀", "다른", "말"}))
    assert AUTO_MEMORY_WEAK_MATCH not in narrowed.excluded
    assert len(narrowed.excluded["QUERY_IRRELEVANT"]) == 6


def test_a_user_correction_still_needs_only_one_shared_word() -> None:
    items = [*pool(), correction("fix", ("프로젝트", "표본이"))]
    narrowed = narrow_recall(items, "프로젝트 이야기", frozenset({"프로젝트", "이야기"}))
    assert ids(narrowed.relevant) == ["fix"]


def test_a_memory_the_question_names_gets_in_without_two_shared_words() -> None:
    items = pool()
    named = items[0].model_copy(update={"source_ref": "HYPOTHESIS:hypothesis:o:special-one"})
    narrowed = narrow_recall(
        [named, *items[1:]], "hypothesis:o:special-one 은?", frozenset({"special"})
    )
    assert ids(narrowed.relevant) == ["p0"]


def test_a_follow_up_question_skips_the_word_gate_and_says_which_marker_it_found() -> None:
    assert follow_up_markers("앞에서 세운 가설과 다음 행동을 정리해 줘") == ("앞에서",)
    assert follow_up_markers("what did the previous run find, and the one above") == (
        "previous",
        "above",
    )
    # an ordinary question and a word that only contains a marker are not follow-ups
    assert follow_up_markers("라이선스를 확인해 줘") == ()
    assert follow_up_markers("aboveboard review") == ()
    assert set(FOLLOW_UP_MARKERS) >= {"앞에서", "아까", "방금", "그 가설", "previous"}
    items = pool()
    narrowed = narrow_recall(items, "앞에서 세운 가설을 정리해 줘", frozenset({"앞에서", "정리해"}))
    assert narrowed.follow_up_markers == ("앞에서",)
    assert len(narrowed.relevant) == 6 and not narrowed.excluded


def test_in_a_follow_up_the_users_correction_comes_first_then_the_newest_memories() -> None:
    items = [*pool(), correction("fix", ("표본이",), minutes=-30)]
    for n, item in enumerate(items[:6]):
        items[n] = item.model_copy(update={"created_at": NOW + timedelta(minutes=n)})
    plan = plan_recall(items, "앞에서 한 일", frozenset(), RecallLimits(), follow_up=True)
    assert ids(plan.selected)[0] == "fix"
    assert ids(plan.selected)[1] == "p5"
    # the usual limits still apply: three hypotheses at most
    assert sum(1 for i in plan.selected if i.kind == MemoryKind.HYPOTHESIS) == 3


def test_an_automatic_hypothesis_or_action_without_evidence_is_one_to_leave_out() -> None:
    bare = memory("bare")
    backed = memory("backed", evidence=("span:1",))
    action = memory("act", kind=MemoryKind.ACTION)
    assert lacks_evidence(bare) and lacks_evidence(action) and not lacks_evidence(backed)
    # a measured outcome is backed by its execution, not by source spans; a correction by its words
    assert not lacks_evidence(memory("out", kind=MemoryKind.FACT))
    assert not lacks_evidence(correction("fix", ("표본이",)))
    assert AUTO_MEMORY_NO_EVIDENCE == "AUTO_MEMORY_NO_EVIDENCE"
