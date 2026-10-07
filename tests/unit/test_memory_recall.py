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
from thoth.domain.memory_terms import words

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
    body: str = "짧은 기억 한 줄 표본",
    minutes: int = 0,
    mode: MemoryPayloadMode = MemoryPayloadMode.DOMAIN_REFERENCE,
    evidence: tuple[str, ...] = (),
) -> FullMemoryRevision:
    """The words a memory is found by come from its text (the line after the reference line)."""
    reference = source if source is not None else f"{kind.value}:{tag}"
    return FullMemoryRevision(
        memory_revision_id=f"rev:{tag}",
        memory_id=f"memory:{tag}",
        project_id="p",
        origin_thread_id="thread:1",
        payload_mode=mode,
        kind=kind,
        owner_revision_ref="a" * 64,
        source_ref=reference,
        content_excerpt=f"{reference}\n{body}",
        scope={},
        evidence_refs=evidence,
        query_terms=(),
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
    plan = plan_recall(items, "표본", words("표본"), RecallLimits())
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
    plan = plan_recall(items, "표본", words("표본"), RecallLimits())
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
    plan = plan_recall(items, "표본", words("표본"), RecallLimits())
    assert all(estimate_tokens(item.content_excerpt) <= 320 for item in plan.included)
    assert len(plan.truncated) == len(plan.included) == 4
    assert plan.tokens <= 1_600
    tight = plan_recall(items, "표본", words("표본"), RecallLimits(total_tokens=700))
    assert len(tight.included) == 2 and tight.omitted_by_limit == {"total_tokens": 2}


def test_the_order_is_id_then_phrase_then_word_hits_then_spread_then_newest_then_id() -> None:
    named = memory("named", source="HYPOTHESIS:hypothesis:o:special-one", body="가나다")
    phrase = memory("phrase", body="표본이 작으면 결론을 보류한다")
    many = memory("many", body="표본이 결론을 보류한다")
    few = memory("few", body="표본이")
    query = (
        "hypothesis:o:special-one 은 어떤가. 표본이 작으면 결론을 보류한다 "
        "를 표본이 결론을 보류한다"
    )
    order = order_recall([few, many, phrase, named], query, words(query))
    assert ids(order) == ["named", "phrase", "many", "few"]
    # equal on the first three: kinds alternate, then newest first, then id
    a1 = memory("a1", kind=MemoryKind.ACTION, minutes=1)
    a2 = memory("a2", kind=MemoryKind.ACTION, minutes=5)
    h1 = memory("h1", kind=MemoryKind.HYPOTHESIS, minutes=2)
    h2 = memory("h2", kind=MemoryKind.HYPOTHESIS, minutes=2)
    assert ids(order_recall([a1, h2, a2, h1], "표본", words("표본"))) == ["a2", "h1", "a1", "h2"]


def test_the_word_hits_are_counted_per_question_word_not_per_letter_pair() -> None:
    # "자료를" has two pairs; a memory holding both of its pairs still meets one question word,
    # so the memory that meets two different question words is ahead of it.
    one_word = memory("one", body="자료를 자료가")
    two_words = memory("two", body="자료 분석")
    query = "자료를 분석을 보여 줘"
    assert ids(order_recall([one_word, two_words], query, words(query))) == ["two", "one"]


def correction(tag: str, text: str, minutes: int = 0) -> FullMemoryRevision:
    """A user's correction: free text under MEMORY_ASSERTION, never an automatic reference."""
    return memory(
        tag,
        kind=MemoryKind.LESSON,
        source=f"MEMORY:{tag}",
        minutes=minutes,
        mode=MemoryPayloadMode.MEMORY_ASSERTION,
    ).model_copy(update={"assertion": text})


def pool(common: str = "프로젝트") -> list[FullMemoryRevision]:
    """Six automatic memories that all mention one project-wide word, each with its own word."""
    own = ("사과", "포도", "수박", "참외", "딸기", "복숭아")
    return [
        memory(f"p{n}", body=f"{own[n]} {common}", source=f"HYP:p{n}", evidence=("span:1",))
        for n in range(6)
    ]


def test_a_word_every_memory_shares_is_a_common_word_and_nothing_else_is() -> None:
    items = pool()
    query = words("프로젝트 사과 이야기")
    assert common_terms(items, query) == frozenset({"프로젝트"})
    # a pool too small to tell what is common says no word is
    assert common_terms(items[:3], query) == frozenset()


def test_an_automatic_memory_that_meets_the_question_on_a_common_word_only_is_left_out() -> None:
    items = pool()
    query = "프로젝트 무관한 질문"
    narrowed = narrow_recall(items, query, words(query))
    assert narrowed.relevant == []
    assert narrowed.excluded[AUTO_MEMORY_WEAK_MATCH] == [i.memory_revision_id for i in items]
    assert narrowed.common_terms == frozenset({"프로젝트"})
    # one word of its own is still only one word
    one = narrow_recall(items, "사과 이야기", words("사과 이야기"))
    assert [i.memory_id for i in one.relevant] == []


def test_two_words_that_are_not_common_let_an_automatic_memory_in() -> None:
    items = pool()
    query = "프로젝트 사과 포도"
    narrowed = narrow_recall(items, query, words(query))
    # p0 meets 사과 only, p1 meets 포도 only: no memory has two of its own words
    assert narrowed.relevant == []
    both = memory("both", body="프로젝트 사과 포도", evidence=("span:1",))
    narrowed = narrow_recall([*items, both], query, words(query))
    assert ids(narrowed.relevant) == ["both"]


def test_a_word_met_through_its_letter_pairs_counts_once_not_twice() -> None:
    # "필요해" and "필요하다" meet through the pair "필요"; that is one question word, so a memory
    # holding only that word is one word short of the two an automatic memory needs.
    items = [memory(f"n{n}", body=f"공통 {n}번째 필요하다", evidence=("span:1",)) for n in range(4)]
    query = "필요해 이거"
    narrowed = narrow_recall(items, query, words(query))
    assert narrowed.relevant == []
    assert len(narrowed.excluded[AUTO_MEMORY_WEAK_MATCH]) == 4


def test_a_memory_that_does_not_meet_the_question_at_all_keeps_the_old_reason() -> None:
    narrowed = narrow_recall(pool(), "전혀 다른 말", words("전혀 다른 말"))
    assert AUTO_MEMORY_WEAK_MATCH not in narrowed.excluded
    assert len(narrowed.excluded["QUERY_IRRELEVANT"]) == 6


def test_a_user_correction_still_needs_only_one_shared_word() -> None:
    items = [*pool(), correction("fix", "프로젝트 표본이 작으면 다시 확인한다")]
    narrowed = narrow_recall(items, "프로젝트 이야기", words("프로젝트 이야기"))
    assert ids(narrowed.relevant) == ["fix"]


def test_a_memory_the_question_names_gets_in_without_two_shared_words() -> None:
    items = pool()
    named = items[0].model_copy(update={"source_ref": "HYPOTHESIS:hypothesis:o:special-one"})
    query = "hypothesis:o:special-one 은?"
    narrowed = narrow_recall([named, *items[1:]], query, words(query))
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
    query = "앞에서 세운 가설을 정리해 줘"
    narrowed = narrow_recall(items, query, words(query))
    assert narrowed.follow_up_markers == ("앞에서",)
    assert len(narrowed.relevant) == 6 and not narrowed.excluded


def test_in_a_follow_up_the_users_correction_comes_first_then_the_newest_memories() -> None:
    items = [*pool(), correction("fix", "표본이 작으면 다시 확인한다", minutes=-30)]
    for n, item in enumerate(items[:6]):
        items[n] = item.model_copy(update={"created_at": NOW + timedelta(minutes=n)})
    plan = plan_recall(items, "앞에서 한 일", words("앞에서 한 일"), RecallLimits(), follow_up=True)
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
    assert not lacks_evidence(correction("fix", "표본이 작으면 다시 확인한다"))
    assert AUTO_MEMORY_NO_EVIDENCE == "AUTO_MEMORY_NO_EVIDENCE"


def test_two_words_met_only_through_letter_pairs_are_not_enough_for_an_automatic_memory() -> None:
    item = memory("pairs", body="실측 결과의 공개 기록", evidence=("span:1",))
    query = "결과물을 공개할 검토"
    narrowed = narrow_recall([item], query, words(query))
    assert narrowed.relevant == []
    assert narrowed.excluded[AUTO_MEMORY_WEAK_MATCH] == [item.memory_revision_id]


def test_one_word_found_as_written_plus_one_through_a_pair_lets_it_in_marked_partial() -> None:
    item = memory("mixed", body="실측 결과의 공개 기록", evidence=("span:1",))
    query = "결과물을 공개 검토"
    narrowed = narrow_recall([item], query, words(query))
    assert ids(narrowed.relevant) == ["mixed"]
    match = narrowed.matches[item.memory_revision_id]
    assert match.matched_by == "QUERY" and match.match_strength == "PARTIAL"
    assert match.partial_words == ("결과물을",) and match.words == ("결과물을", "공개")
    whole = narrow_recall([item], "실측 공개 검토", words("실측 공개 검토"))
    strong = whole.matches[item.memory_revision_id]
    assert strong.match_strength == "WHOLE" and strong.partial_words == ()


def test_a_users_correction_is_not_held_to_the_found_as_written_rule() -> None:
    fix = correction("fix", "실측 결과의 공개 기록")
    query = "결과물을 이야기"
    narrowed = narrow_recall([fix], query, words(query))
    assert ids(narrowed.relevant) == ["fix"]
    assert narrowed.matches[fix.memory_revision_id].match_strength == "PARTIAL"


def test_added_words_need_one_found_as_written_and_a_pair_only_one_is_marked_weak() -> None:
    from thoth.domain.memory_terms import added_words

    item = memory("a1", body="원문과 표를 읽기 전용으로 대조한다", evidence=("span:1",))
    query = "대조 방법"
    own = words(query)
    # "원문" is found as written in the memory; "원문서" meets it only through the pair "원문"
    strong = narrow_recall([item], query, own, added_words("원문 검토", own))
    match = strong.matches[item.memory_revision_id]
    assert ids(strong.relevant) == ["a1"] and match.matched_by == "EXPANSION"
    assert match.added_words == ("원문",) and match.partial_added_words == ()
    weak = narrow_recall([item], query, own, added_words("원문서 검토", own))
    assert weak.relevant == [] and weak.excluded[AUTO_MEMORY_WEAK_MATCH] == [
        item.memory_revision_id
    ]
    # a pair-only added word beside a whole one is let in and marked
    both = narrow_recall([item], query, own, added_words("원문 전용서", own))
    marked = both.matches[item.memory_revision_id]
    assert marked.match_strength == "PARTIAL" and marked.partial_added_words == ("전용서",)
