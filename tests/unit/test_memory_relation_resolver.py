"""A relation the rules cannot settle is held, or put to a model a limited number of times."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

import pytest

from thoth.application.services.memory_relation_judge import ModelMemoryRelationJudge
from thoth.application.services.memory_relation_resolver import (
    MAX_MODEL_RELATION_CALLS,
    MemoryRelationBudget,
    MemoryRelationResolver,
    RelationVerdict,
)
from thoth.domain.enums import MemoryKind, MemoryPayloadMode, ModelRole
from thoth.domain.memory import (
    FullMemoryRevision,
    MemoryReviewRole,
    MemoryReviewVerdict,
    MemoryRoleReview,
    MemoryTransition,
)
from thoth.domain.memory_relation import (
    MemoryRelation,
    MemoryRelationProposal,
    MemoryRelationQuestion,
)
from thoth.domain.model import ModelRequest, ModelResult

NOW = datetime(2026, 9, 30, tzinfo=UTC)
ROOT = "1" * 64
NEW_TEXT = "표본이 크면 결론을 확정한다"
OLD_TEXT = "표본이 작으면 결론을 보류한다"


class NoLedger:
    def read_revision_by_digest(self, project_id: str, digest: str) -> None:
        return None


def correction(tag: str, text: str = OLD_TEXT) -> FullMemoryRevision:
    return FullMemoryRevision(
        memory_revision_id=f"rev:{tag}",
        memory_id=f"memory:{tag}",
        project_id="p",
        origin_thread_id="thread:1",
        payload_mode=MemoryPayloadMode.MEMORY_ASSERTION,
        kind=MemoryKind.LESSON,
        owner_revision_ref="a" * 64,
        source_ref=f"MEMORY:{tag}",
        assertion=text,
        content_excerpt=f"MEMORY:{tag}\n{text}",
        scope={},
        evidence_refs=(),
        query_terms=("표본이",),
        support_status="SUPPORTED",
        authority_status="AUTHORITATIVE",
        cutoff_at=NOW,
        cutoff_valid=True,
        reviews=tuple(
            MemoryRoleReview(
                role=role, verdict=MemoryReviewVerdict.PASS, reason_code="OK", basis_digest="b" * 64
            )
            for role in MemoryReviewRole
        ),
        transition=MemoryTransition.COMMIT,
        recall_eligible=True,
        action_eligible=False,
        parent_revision_digest=ROOT,
        revision_digest=(tag.encode().hex() * 64)[:64],
        created_at=NOW,
    )


class Judge:
    def __init__(self, proposal: MemoryRelationProposal | Exception) -> None:
        self.proposal, self.questions = proposal, []
        self.questions: list[MemoryRelationQuestion] = []

    async def judge(self, question: MemoryRelationQuestion) -> MemoryRelationProposal:
        self.questions.append(question)
        if isinstance(self.proposal, Exception):
            raise self.proposal
        return self.proposal


def cited(relation: MemoryRelation) -> MemoryRelationProposal:
    return MemoryRelationProposal(
        relation=relation, reason="이유", first_span="표본이", second_span="표본이"
    )


async def resolve(
    judge: Judge | None,
    existing: list[FullMemoryRevision],
    budget: MemoryRelationBudget | None = None,
) -> RelationVerdict:
    resolver = MemoryRelationResolver(cast(Any, NoLedger()), judge)
    by_digest = {item.revision_digest: item for item in existing}
    new = resolver.candidate_subject(
        project_id="p",
        memory_id="memory:new",
        kind=MemoryKind.LESSON,
        owner_revision_ref="a" * 64,
        source_ref="MEMORY:new",
        scope={},
        content=None,
        assertion=NEW_TEXT,
        staged_parents={},
        correction_root=ROOT,
    )
    return await resolver.resolve(
        new, NEW_TEXT, existing, by_digest, budget or MemoryRelationBudget()
    )


@pytest.mark.asyncio
async def test_without_a_model_an_ambiguous_relation_is_held_and_nothing_is_called() -> None:
    verdict = await resolve(None, [correction("a")])
    assert verdict.ambiguous and verdict.conflict and not verdict.contradiction
    assert [j.outcome for j in verdict.judgments] == ["HELD_NO_MODEL"]


@pytest.mark.asyncio
async def test_a_related_but_settled_relation_never_reaches_a_model() -> None:
    judge = Judge(cited(MemoryRelation.UNRELATED))
    same_words = correction("a", " 표본이  크면 결론을 확정한다")
    unrelated_root = correction("b").model_copy(update={"parent_revision_digest": "2" * 64})
    verdict = await resolve(judge, [same_words, unrelated_root])
    assert not verdict.conflict and verdict.judgments == () and judge.questions == []


@pytest.mark.asyncio
async def test_at_most_three_model_calls_then_the_rest_are_held() -> None:
    judge = Judge(cited(MemoryRelation.UNRELATED))
    existing = [
        correction(f"c{n}", f"표본이 다르다 {n}") for n in range(MAX_MODEL_RELATION_CALLS + 2)
    ]
    budget = MemoryRelationBudget()
    verdict = await resolve(judge, existing, budget)
    outcomes = [j.outcome for j in verdict.judgments]
    assert outcomes == ["MODEL_APPLIED"] * 3 + ["HELD_OVER_LIMIT"] * 2
    assert len(judge.questions) == 3 and budget.used == 3
    assert verdict.ambiguous
    # the same budget carries into a later preparation of the same investigation
    later = await resolve(judge, [correction("z", "표본이 또 다르다")], budget)
    assert [j.outcome for j in later.judgments] == ["HELD_OVER_LIMIT"] and len(judge.questions) == 3


@pytest.mark.asyncio
async def test_an_answer_without_quoted_evidence_or_a_failure_is_held() -> None:
    no_span = Judge(MemoryRelationProposal(relation=MemoryRelation.UNRELATED, reason="이유"))
    assert [j.outcome for j in (await resolve(no_span, [correction("a")])).judgments] == [
        "HELD_NO_EVIDENCE"
    ]
    invented = Judge(
        MemoryRelationProposal(
            relation=MemoryRelation.UNRELATED,
            reason="이유",
            first_span="없는 문장",
            second_span="표본이",
        )
    )
    held = await resolve(invented, [correction("a")])
    assert held.ambiguous and held.judgments[0].outcome == "HELD_NO_EVIDENCE"
    broken = await resolve(Judge(RuntimeError("down")), [correction("a")])
    assert broken.ambiguous and broken.judgments[0].outcome == "HELD_MODEL_FAILED"
    undecided = await resolve(Judge(cited(MemoryRelation.AMBIGUOUS)), [correction("a")])
    assert undecided.ambiguous and undecided.judgments[0].outcome == "MODEL_UNDECIDED"


@pytest.mark.asyncio
async def test_a_quoted_answer_is_only_a_proposal_and_a_contradiction_still_holds() -> None:
    settled = await resolve(Judge(cited(MemoryRelation.UNRELATED)), [correction("a")])
    assert not settled.conflict
    assert settled.judgments[0].authority == "PROPOSAL_ONLY"
    contradiction = await resolve(
        Judge(cited(MemoryRelation.CONTRADICTION_CANDIDATE)), [correction("a")]
    )
    assert contradiction.contradiction and contradiction.conflict


@pytest.mark.asyncio
async def test_the_order_the_model_sees_is_not_fixed_and_is_recorded() -> None:
    judge = Judge(cited(MemoryRelation.UNRELATED))
    verdict = await resolve(judge, [correction(f"o{n}", f"표본이 {n}") for n in range(3)])
    orders = {j.order for j in verdict.judgments}
    assert orders <= {"CANDIDATE_FIRST", "EXISTING_FIRST"}
    for judgment, question in zip(verdict.judgments, judge.questions, strict=True):
        first = "memory:new" if judgment.order == "CANDIDATE_FIRST" else judgment.other_memory_id
        assert question.first.memory_id == first
    many = [correction(f"p{n:02d}", f"표본이 {n}") for n in range(12)]
    varied = await resolve(
        Judge(cited(MemoryRelation.UNRELATED)), many, MemoryRelationBudget(limit=12)
    )
    assert {j.order for j in varied.judgments} == {"CANDIDATE_FIRST", "EXISTING_FIRST"}


class RecordingModel:
    def __init__(self) -> None:
        self.requests: list[ModelRequest[Any]] = []

    async def structured(self, request: ModelRequest[Any]) -> ModelResult[Any]:
        self.requests.append(request)
        return ModelResult(
            output=cited(MemoryRelation.UNRELATED),
            model_id="fake",
            prompt_version=request.prompt_version,
            scripted=True,
            input_digest="i" * 64,
            output_digest="o" * 64,
        )


@pytest.mark.asyncio
async def test_the_model_judge_uses_the_research_model_port_with_a_bounded_request() -> None:
    model = RecordingModel()
    judge = ModelMemoryRelationJudge(
        model,
        project_id="p",
        cutoff_at=NOW,
        model_policy_ref="policy:1",
        head_set_digest="h" * 64,
    )
    verdict = await resolve(cast(Any, judge), [correction("a")])
    assert not verdict.conflict
    (request,) = model.requests
    assert request.role == ModelRole.MEMORY_RELATION_JUDGE
    assert request.output_model is MemoryRelationProposal
    assert request.max_output_tokens <= 800 and request.model_policy_ref == "policy:1"
    context = request.context_pack.research_context
    assert {"first_memory", "second_memory", "task"} <= set(context)
