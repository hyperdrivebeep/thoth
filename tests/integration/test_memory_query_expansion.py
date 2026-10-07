"""A model may add search words to a question once per investigation; recall stays bounded by rules.

The expander here is a fake. These tests check what recall does with added words, that the call is
made once and only when it can help, and that a failed call changes nothing.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import pytest
from pydantic import ValidationError
from tests.integration.test_a02_autonomous_acquisition import prepare_thread, request, value
from tests.integration.test_memory_edit import Json
from tests.integration.test_memory_selection import (
    CUTOFF,
    Clock,
    Ids,
    Injection,
    Ledger,
    Store,
    stored,
)

from thoth.application.services.full_project_memory import FullProjectMemoryService
from thoth.application.services.memory_query_expansion import widen_query
from thoth.application.services.research_usage import summarize_operation_usage
from thoth.domain.enums import MemoryKind
from thoth.domain.memory import FullMemoryContextPack, FullMemoryRevision
from thoth.domain.memory_expansion import (
    MEMORY_QUERY_EXPANSION_PURPOSE,
    MemoryExpansionOutcome,
    MemoryExpansionRecord,
    QueryExpansion,
)
from thoth.domain.memory_terms import added_words, words
from thoth.domain.model_dispatch import CONTROLLED_MODEL_CONTROL, ModelDispatchRecord
from thoth.ports.model import ModelOutputContractHold

QUESTION = "출처별 대조 분석(A1)을 지금 바로 해도 돼? 무엇이 더 필요해?"
# The memory the question is about. It holds one question word ("대조") and none of "분석".
A1_TEXT = "연결된 RFP PDF 원문과 표를 읽기 전용으로 대조한다."
WIDENING = QueryExpansion(
    synonyms=("원문 검토", "문서 비교"),
    keywords=("source review",),
    related=("근거 확인",),
    note_line="원문 문서를 읽기 전용으로 비교하는 행동",
)


def memories() -> list[FullMemoryRevision]:
    return [
        stored("a1", kind=MemoryKind.ACTION, excerpt=f"ACTION:a1\n{A1_TEXT}", minutes=1),
        stored(
            "h1",
            excerpt="HYPOTHESIS:h1\n센서별 가시성 라벨 설계는 항만 시험으로 검증한다",
            minutes=2,
        ),
        stored("h2", excerpt="HYPOTHESIS:h2\n표본이 작으면 결론을 보류한다", minutes=3),
    ]


@dataclass
class Switch:
    on: bool = True

    def expansion_enabled(self, project_id: str) -> bool:
        return self.on


@dataclass
class FakeExpander:
    answer: QueryExpansion | None = WIDENING
    error: BaseException | None = None
    delay: float = 0.0
    calls: int = field(default=0)

    async def expand(self, query: str) -> QueryExpansion:
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        assert self.answer is not None
        return self.answer


def service_for(
    items: list[FullMemoryRevision], *, memory_on: bool = True, widen_on: bool = True
) -> tuple[FullProjectMemoryService, Store]:
    store = Store(items)
    contents = {i.owner_revision_ref: {"statement": "내용"} for i in items}
    svc = FullProjectMemoryService(
        store=cast(object, store),  # type: ignore[arg-type]
        candidates=cast(object, None),  # type: ignore[arg-type]
        ledger=cast(object, Ledger(contents)),  # type: ignore[arg-type]
        clock=cast(object, Clock()),  # type: ignore[arg-type]
        ids=cast(object, Ids()),  # type: ignore[arg-type]
        injection=Injection(memory_on),
        expansion_switch=Switch(widen_on),
    )
    return svc, store


def recall(
    svc: FullProjectMemoryService,
    outcome: MemoryExpansionOutcome | None,
    query: str = QUESTION,
) -> FullMemoryContextPack:
    return svc.build_context(
        project_id="p",
        thread_id="thread:q",
        query=query,
        target_use="WORKING_CONTEXT",
        scope={},
        cutoff_at=CUTOFF,
        expansion=outcome,
    )


async def widen(
    svc: FullProjectMemoryService, expander: FakeExpander, query: str = QUESTION, **more: float
) -> MemoryExpansionOutcome:
    return await widen_query(
        memory=svc,
        expander=expander,
        work=None,
        project_id="p",
        query=query,
        **more,
    )


@pytest.mark.asyncio
async def test_an_added_word_lets_a_question_find_a_memory_it_shares_only_one_word_with() -> None:
    svc, _ = service_for(memories())
    plain = recall(svc, None)
    assert plain.included == ()
    assert plain.selection is not None and plain.selection.matches == {}
    outcome = await widen(svc, FakeExpander())
    pack = recall(svc, outcome)
    assert [i.memory_id for i in pack.included] == ["memory:a1"]
    selection = pack.selection
    assert selection is not None and selection.expansion is not None
    match = selection.matches["rev:a1"]
    assert match.matched_by == "EXPANSION" and match.words == ("대조",)
    assert "원문" in match.added_words
    assert selection.expansion.status == "USED" and "원문" in selection.expansion.added_words
    assert "대조" not in selection.expansion.added_words


@pytest.mark.asyncio
async def test_a_memory_the_questions_own_words_reach_comes_before_one_only_added_words_reach() -> (
    None
):
    own = stored("own", kind=MemoryKind.ACTION, excerpt="ACTION:own\n출처별 분석 결과를 정리한다")
    added = stored("added", kind=MemoryKind.ACTION, excerpt=f"ACTION:added\n{A1_TEXT}", minutes=9)
    svc, _ = service_for([added, own, *memories()[1:]])
    pack = recall(svc, await widen(svc, FakeExpander()))
    assert [i.memory_id for i in pack.included][:2] == ["memory:own", "memory:added"]
    assert pack.selection is not None
    assert pack.selection.matches["rev:own"].matched_by == "QUERY"
    assert pack.selection.matches["rev:added"].matched_by == "EXPANSION"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "status"),
    [
        (RuntimeError("boom"), "EXPANSION_FAILED"),
        (ModelOutputContractHold("MODEL_OUTPUT_INVALID"), "EXPANSION_INVALID"),
    ],
)
async def test_a_failed_or_malformed_answer_leaves_recall_as_it_was_and_says_why(
    error: BaseException, status: str
) -> None:
    svc, _ = service_for(memories())
    outcome = await widen(svc, FakeExpander(error=error))
    assert outcome.record.status == status and outcome.expansion is None
    failed = recall(svc, outcome)
    plain = recall(svc, None)
    assert failed.included == plain.included and failed.excluded_reason_counts == (
        plain.excluded_reason_counts
    )
    assert failed.selection is not None and failed.selection.expansion is not None
    assert failed.selection.expansion.status == status
    assert failed.selection.matches == {}


@pytest.mark.asyncio
async def test_an_answer_in_the_wrong_shape_is_invalid_and_a_slow_one_times_out() -> None:
    svc, _ = service_for(memories())
    with pytest.raises(ValidationError) as caught:
        QueryExpansion.model_validate({"synonyms": "not a list"})
    invalid = await widen(svc, FakeExpander(error=caught.value))
    assert invalid.record.status == "EXPANSION_INVALID"
    slow = await widen(svc, FakeExpander(delay=5), timeout_seconds=0.01)
    assert slow.record.status == "EXPANSION_FAILED" and slow.record.reason == "TIMEOUT"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "reason"),
    [
        ("memory_off", "MEMORY_OFF"),
        ("setting_off", "SETTING_OFF"),
        ("no_candidates", "NO_CANDIDATES"),
        ("follow_up", "FOLLOW_UP"),
    ],
)
async def test_no_model_call_is_made_when_it_cannot_help(kind: str, reason: str) -> None:
    items = [] if kind == "no_candidates" else memories()
    svc, _ = service_for(items, memory_on=kind != "memory_off", widen_on=kind != "setting_off")
    expander = FakeExpander()
    query = "앞에서 세운 가설을 정리해 줘" if kind == "follow_up" else QUESTION
    outcome = await widen(svc, expander, query)
    assert expander.calls == 0
    assert outcome.record.status == "NOT_ASKED" and outcome.record.reason == reason
    assert recall(svc, outcome, query).included == recall(svc, None, query).included


@pytest.mark.asyncio
async def test_an_investigation_asks_once_however_many_steps_recall_memory() -> None:
    from thoth.domain.research_execution import ResearchWork

    svc, _ = service_for(memories())
    work = ResearchWork.__new__(ResearchWork)
    work.preprocessing_cache = {}
    expander = FakeExpander()
    first = await widen_query(
        memory=svc, expander=expander, work=work, project_id="p", query=QUESTION
    )
    second = await widen_query(
        memory=svc, expander=expander, work=work, project_id="p", query=QUESTION
    )
    assert expander.calls == 1 and first is second
    # a different investigation (new work) asks again
    other = ResearchWork.__new__(ResearchWork)
    other.preprocessing_cache = {}
    await widen_query(memory=svc, expander=expander, work=other, project_id="p", query=QUESTION)
    assert expander.calls == 2


def test_a_word_the_question_already_has_is_never_counted_a_second_time() -> None:
    query = words("자료를 정리해 줘")
    added = [w.run for w in added_words("자료 자료를 정리 분석 분석을 데이터", query)]
    # 자료 / 자료를 / 정리 are the question's; 분석 and 분석을 are one word added once
    assert added == ["분석", "데이터"]


def test_the_models_words_are_limited_before_they_reach_matching() -> None:
    many = QueryExpansion.model_validate(
        {
            "synonyms": [f"단어{n}" for n in range(12)],
            "keywords": ["x" * 41, "ok"],
            "related": [],
            "note_line": "가" * 500,
        }
    )
    assert len(many.synonyms) == 8 and many.keywords == ("ok",) and len(many.note_line) == 240


def test_the_expansions_usage_is_shown_apart_inside_the_operations_total() -> None:
    def record(dispatch_id: str, purpose: str | None, tokens: int) -> ModelDispatchRecord:
        return ModelDispatchRecord(
            dispatch_id=dispatch_id,
            thread_id="thread:q",
            operation_id="op:1",
            capability=CONTROLLED_MODEL_CONTROL,
            payload_bytes=10,
            payload_digest="d",
            output_reserved=100,
            input_tokens=tokens,
            output_tokens=tokens // 10,
            purpose=purpose,
        )

    usage = summarize_operation_usage(
        [
            record("c:0", None, 1000),
            record("c:1", MEMORY_QUERY_EXPANSION_PURPOSE, 200),
        ],
        "thread:q",
        "op:1",
    )
    assert usage is not None
    assert usage["calls"] == 2 and usage["input_tokens"] == 1200 and usage["output_tokens"] == 120
    by_purpose = cast(dict[str, dict[str, object]], usage["by_purpose"])
    entry = by_purpose[MEMORY_QUERY_EXPANSION_PURPOSE]
    assert entry["label"] == "기억 검색어 넓히기"
    assert (entry["calls"], entry["input_tokens"], entry["output_tokens"]) == (1, 200, 20)
    plain = summarize_operation_usage([record("c:0", None, 1000)], "thread:q", "op:1")
    assert plain is not None and "by_purpose" not in plain


@pytest.mark.asyncio
async def test_the_model_expander_asks_with_its_own_role_a_small_limit_and_marks_the_call() -> None:
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from thoth.application.services.memory_query_expansion import ModelMemoryQueryExpander
    from thoth.domain.enums import ModelRole
    from thoth.domain.research_execution import model_purpose

    seen: dict[str, object] = {}

    class Model:
        async def structured(self, request: object) -> object:
            seen["request"] = request
            seen["purpose"] = model_purpose.get()
            return SimpleNamespace(output=WIDENING, dispatch_ids=("call:0",))

    expander = ModelMemoryQueryExpander(
        cast(object, Model()),  # type: ignore[arg-type]
        project_id="p",
        cutoff_at=datetime(2026, 9, 1, tzinfo=UTC),
        model_policy_ref="policy:1",
        head_set_digest="h" * 64,
    )
    assert await expander.expand(QUESTION) == WIDENING
    request_seen = cast(SimpleNamespace, seen["request"])
    assert request_seen.role == ModelRole.MEMORY_QUERY_EXPANDER
    assert request_seen.max_output_tokens == 300 and request_seen.output_model is QueryExpansion
    assert request_seen.context_pack.problem == QUESTION
    assert seen["purpose"] == MEMORY_QUERY_EXPANSION_PURPOSE
    assert model_purpose.get() is None and expander.dispatch_ids == ("call:0",)


@pytest.mark.asyncio
async def test_the_widening_switch_is_read_and_changed_apart_from_the_memory_switch(
    tmp_path: Path,
) -> None:
    runtime, _connector, project = await prepare_thread(tmp_path, allow_connector=True)
    try:

        async def call(method: str, key: str, **more: object) -> Json:
            payload = cast(dict[str, object], {"project_id": project, **more})
            return value(await runtime.bus.dispatch(request(method, key, payload)))  # type: ignore[arg-type]

        first = await call("memory/settings/read", "qe-read-1")
        assert first["query_expansion"] is True
        off = await call(
            "memory/settings/update",
            "qe-off",
            memory_injection=True,
            query_expansion=False,
            expected_digest=None,
        )
        assert off["query_expansion"] is False and off["memory_injection"] is True
        # changing only the memory switch keeps the widening choice
        digest = cast(str, off["settings_digest"])
        kept = await call(
            "memory/settings/update", "qe-keep", memory_injection=False, expected_digest=digest
        )
        assert kept["memory_injection"] is False and kept["query_expansion"] is False
    finally:
        runtime.close()


def test_the_recorded_outcome_keeps_what_the_selection_record_needs() -> None:
    outcome = MemoryExpansionOutcome(
        record=MemoryExpansionRecord(status="USED", added_words=("원문",), dispatch_ids=("c:0",)),
        expansion=WIDENING,
    )
    assert outcome.used and MemoryExpansionOutcome().used is False
