"""Normal RPC/TUI entry tests with controlled models, not live quality claims."""

import asyncio
from pathlib import Path
from typing import cast

import pytest
from pydantic import BaseModel
from tests.integration.scoped_runtime import fixture_scope_policy
from tests.integration.storage_coverage_helpers import request, value

from thoth.adapters.connectors import ConnectorRegistry
from thoth.adapters.runtime import SystemClock
from thoth.adapters.storage import SqliteConversationSessionStore
from thoth.application.services.conversation_router import ConversationRouter
from thoth.application.services.tui_session_service import TuiSessionService
from thoth.apps.conversation_dispatch import BusConversationDispatcher
from thoth.apps.runtime import create_runtime
from thoth.domain.action import (
    ActionDraft,
    ActionPlanDraft,
    ActionRiskFacts,
    DecisionAnalysis,
    DecisionCriterion,
)
from thoth.domain.canonical import model_digest
from thoth.domain.enums import (
    CausalDepth,
    HypothesisStatus,
    ModelRole,
    PortfolioStatus,
    Reversibility,
)
from thoth.domain.evidence_requirements import (
    EvidenceRanking,
    EvidenceRequirement,
    HypothesisSemanticDecision,
    HypothesisSemanticReview,
    RequirementProposal,
    RequirementSetRevision,
    ResearchSourcePlan,
    ReviewAdjudication,
    ReviewProposal,
    SemanticReviewCandidate,
    SemanticReviewDecision,
    SourceSelector,
)
from thoth.domain.hypothesis import Hypothesis, HypothesisPortfolio
from thoth.domain.model import ModelRequest, ModelResult
from thoth.domain.model_dispatch import CONTROLLED_MODEL_CONTROL


class ControlledResearchModel:
    control_capability = CONTROLLED_MODEL_CONTROL

    def __init__(
        self,
        *,
        wait: bool = False,
        missing: bool = False,
        one: bool = False,
        na: bool = False,
        discover: bool = False,
    ) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        if not wait:
            self.release.set()
        self.missing = missing
        self.one, self.na, self.discover = one, na, discover
        self.calls: list[ModelRequest[BaseModel]] = []

    def resolve(self, *, provider: str, model: str | None = None):
        return self

    async def structured[T: BaseModel](self, request: ModelRequest[T]) -> ModelResult[T]:
        req = request
        self.calls.append(cast(ModelRequest[BaseModel], req))
        ctx = req.context_pack
        if req.role == ModelRole.RESEARCH_PLANNER:
            self.started.set()
            await self.release.wait()
            output = RequirementProposal(
                profile_candidates=("DOCUMENT_QUESTION:1",),
                expanded_queries=("latency 지연 ms",),
                checks=()
                if not self.na
                else (
                    EvidenceRequirement(
                        requirement_id="optional",
                        kind="RESEARCH_CHECK",
                        target="optional",
                        question="Optional unneeded field",
                        rationale="Check applicability",
                        needed_for="optional",
                        blocker="research",
                        followup="Inspect current conditions",
                    ),
                ),
            )
        elif req.role == ModelRole.EVIDENCE_RERANKER:
            output = EvidenceRanking(
                ordered_span_ids=tuple(s.span_id for s in ctx.evidence),
                rationale="Controlled relevance ordering",
            )
        elif req.role == ModelRole.SEMANTIC_REVIEWER:
            requirements = RequirementSetRevision.model_validate(
                ctx.research_context["requirements"]
            ).requirements
            output = ReviewProposal(
                candidates=tuple(
                    SemanticReviewCandidate(
                        requirement_id=r.requirement_id,
                        evidence_refs=tuple(s.span_id for s in ctx.evidence),
                        relation="INSUFFICIENT" if self.missing or not ctx.evidence else "SUPPORTS",
                        applicability="NOT_APPLICABLE_CANDIDATE" if self.na else "APPLICABLE",
                        applicability_basis=tuple(s.span_id for s in ctx.evidence)
                        if self.na
                        else (),
                        explanation="Condition/time checked in controlled input",
                        uncertainty="Controlled model does not establish live semantic quality",
                        conditions_checked=True,
                        time_checked=True,
                        counterevidence_checked=True,
                    )
                    for r in requirements
                ),
                answer="A bounded answer with explicit evidence limits",
            )
        elif req.role == ModelRole.REVIEW_ADJUDICATOR:
            candidates = ReviewProposal.model_validate(ctx.research_context["candidate"]).candidates
            output = ReviewAdjudication(
                decisions=tuple(
                    SemanticReviewDecision(
                        requirement_id=c.requirement_id,
                        verdict="APPLIED",
                        applicability_confirmed=self.na,
                        explanation="Separate controlled role",
                    )
                    for c in candidates
                ),
                decomposition_complete=True,
            )
        elif req.role == ModelRole.SOURCE_PLANNER:
            output = ResearchSourcePlan(
                reason="Bounded source route",
                selectors=(
                    SourceSelector.model_validate(
                        {
                            "connector_id": "a02-readonly",
                            "selector": {"relative_path": "catalog.md"},
                        }
                    ),
                )
                if self.discover
                else (),
            )
        elif req.role == ModelRole.HYPOTHESIS_GENERATOR:
            output = HypothesisPortfolio(
                portfolio_id=f"portfolio:{ctx.object_id}",
                object_id=ctx.object_id,
                hypotheses=()
                if not self.one
                else (
                    Hypothesis(
                        hypothesis_id=f"hypothesis:{ctx.object_id}",
                        object_id=ctx.object_id,
                        statement="A conditional predictive candidate",
                        observed_problem=ctx.problem,
                        primary_locus=None,
                        primary_intent="PREDICTIVE",
                        causal_depth=CausalDepth.UNDETERMINED,
                        scope_conditions={"condition": "alpha"},
                        support_evidence_refs=tuple(s.span_id for s in ctx.evidence),
                        counterevidence_refs=(),
                        counterevidence_queries=("Check counterexamples",),
                        missing_evidence=("Discriminating observations",),
                        assumptions=("Conditions remain stable",),
                        uncertainty="Not validated",
                        predicted_observations=(),
                        discriminating_tests=(),
                        status=HypothesisStatus.DRAFT,
                    ),
                ),
                status=PortfolioStatus.DRAFT,
                generated_from_head_set=ctx.input_head_set_digest,
                alternatives_considered=("Observation alone cannot isolate a cause",),
                next_checks=("Collect a discriminating observation",),
                uncertainty_reserve="Cause unknown",
            )
        elif req.role == ModelRole.HYPOTHESIS_REVIEWER:
            output = HypothesisSemanticReview(
                decisions=tuple(
                    HypothesisSemanticDecision(
                        hypothesis_id=h.hypothesis_id,
                        relation="INCONCLUSIVE",
                        explanation="Unverified predictive candidate",
                        gaps=("Additional validation required",),
                    )
                    for h in (
                        ()
                        if ctx.candidate_portfolio is None
                        else ctx.candidate_portfolio.hypotheses
                    )
                ),
                alternatives_considered=("Cause unknown",),
                next_checks=("Inspect source context",),
                uncertainty_reserve="Unassessed",
            )
        elif req.role == ModelRole.ACTION_PLANNER:
            assert "hypothesis_review" in ctx.research_context
            output = ActionPlanDraft(
                plan_id=f"plan:{ctx.object_id}",
                object_id=ctx.object_id,
                alternatives=tuple(
                    ActionDraft(
                        action_id=f"action:{ctx.object_id}:{i}",
                        object_id=ctx.object_id,
                        hypothesis_ids=tuple(
                            h.hypothesis_id
                            for h in (
                                ()
                                if ctx.candidate_portfolio is None
                                else ctx.candidate_portfolio.hypotheses
                            )
                        ),
                        action_family=family,
                        specification=description,
                        expected_information_value="Resolve an explicit information gap",
                        reversibility=Reversibility.FULL,
                        effect_facts=ActionRiskFacts(),
                        effect_completeness_confirmed=True,
                        source_refs=tuple(s.span_id for s in ctx.evidence),
                        missing_evidence=("Unresolved causal evidence",),
                    )
                    for i, (family, description) in enumerate(
                        (
                            ("EVIDENCE_REQUEST", "Collect original records"),
                            ("READ_ONLY_ANALYSIS", "Compare conditions"),
                        )
                    )
                ),
                decision_analysis=DecisionAnalysis(
                    decision="Next bounded check",
                    criteria=(
                        DecisionCriterion(
                            criterion_id="information",
                            name="Information value",
                            mandatory=True,
                            rationale="Resolve the current gap",
                        ),
                    ),
                    evaluations=(),
                    uncertainty="Costs unknown",
                    sensitivity="Preference unavailable; show alternatives",
                ),
                proposed_frontier=(f"action:{ctx.object_id}:0", f"action:{ctx.object_id}:1"),
                plan_revision_digest=ctx.input_head_set_digest,
            )
        else:
            raise AssertionError(req.role)
        parsed = req.output_model.model_validate(output.model_dump())
        return ModelResult(
            output=parsed,
            model_id="CONTROLLED_RESEARCH",
            scripted=True,
            prompt_version=req.prompt_version,
            input_digest=model_digest("INPUT", ctx, schema_version="1.0.0"),
            output_digest=model_digest("OUTPUT", parsed, schema_version="1.0.0"),
        )


async def setup(
    tmp_path: Path,
    model: ControlledResearchModel,
    *,
    source: bool = True,
    connector_registry: ConnectorRegistry | None = None,
):
    runtime = create_runtime(
        tmp_path,
        model_resolver=model,
        resource_scope_policy=fixture_scope_policy(),
        connector_registry=connector_registry,
    )
    value(
        await runtime.bus.dispatch(
            request(
                "project/create",
                "project",
                {
                    "project_id": "p",
                    "name": "Request integration",
                    "cutoff_at": "2026-09-13T00:00:00Z",
                },
            )
        )
    )
    if source:
        (tmp_path / "inbox").mkdir(exist_ok=True)
        (tmp_path / "inbox" / "records.html").write_text(
            '<html><head><meta property="article:published_time" '
            'content="2026-09-01T00:00:00Z" /></head><body>'
            "<h1>Latency report</h1><p>단위 ms; 조건 alpha</p>"
            "<p>기록 ID LAB-42: 지연 12 ms</p>"
            "<p>반증: 다른 조건에서 재현하지 못함</p></body></html>",
            encoding="utf-8",
        )
        value(
            await runtime.bus.dispatch(
                request(
                    "project/source/connect",
                    "source",
                    {
                        "project_id": "p",
                        "relative_path": "records.html",
                        "media_type": "text/html",
                        "authority": "INFORMAL",
                        "cutoff_state": "ELIGIBLE",
                        "security_class": "INTERNAL",
                    },
                )
            )
        )
    return runtime


@pytest.mark.asyncio
async def test_first_tui_input_is_durably_accepted_then_researched(tmp_path: Path):
    model = ControlledResearchModel(wait=True)
    runtime = await setup(tmp_path, model)
    try:
        tui = TuiSessionService(
            session_id="test:tui",
            store=SqliteConversationSessionStore(runtime.ledger.engine),
            router=ConversationRouter(),
            dispatcher=BusConversationDispatcher(runtime.bus),
            clock=SystemClock(),
        )
        turn = await tui.execute("LAB-42의 지연과 조건을 알려줘")
        assert turn.response["status"] == "ACCEPTED_RUNNING"
        operation = str(turn.response["operation_id"])
        thread = str(turn.response["thread_id"])
        running = runtime.bus.read_operation(operation)
        assert running is not None and running.state.value == "RUNNING"
        try:
            await asyncio.wait_for(model.started.wait(), 10)
        except TimeoutError:
            pytest.fail(str(runtime.bus.read_operation(operation)))
        status = value(
            await runtime.bus.dispatch(
                request("thread/read", "read-before", {"project_id": "p", "thread_id": thread})
            )
        )
        assert status["request"]["effective_question"].count("LAB-42") == 1
        assert status["inputs"][0]["state"] == "ACCEPTED"
        model.release.set()
        await runtime.bus.drain()
        op = runtime.bus.read_operation(operation)
        assert op is not None
        assert op.state.value == "SUCCEEDED", op.error
        status = value(
            await runtime.bus.dispatch(
                request("thread/read", "read-after", {"project_id": "p", "thread_id": thread})
            )
        )
        assert status["current_result"]["completion"] == "TERMINAL"
        assert status["inputs"][0]["state"] == "APPLIED"
        assert status["current_result"]["result"]["portfolio"]["hypotheses"] == []
        assert (
            status["current_result"]["result"]["coverage"]["web_decision"] == "SKIPPED_SUFFICIENT"
        )
        roles = [c.role for c in model.calls]
        assert roles.index(ModelRole.HYPOTHESIS_REVIEWER) < roles.index(ModelRole.ACTION_PLANNER)
        listed = value(
            await runtime.bus.dispatch(
                request("hypothesis/portfolio/list", "portfolios", {"project_id": "p"})
            )
        )
        assert listed
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_unconnected_question_and_reopen_have_no_fabricated_evidence(tmp_path: Path):
    model = ControlledResearchModel()
    runtime = await setup(tmp_path, model, source=False)
    reply = await runtime.bus.dispatch(
        request(
            "thread/start",
            "start",
            {"project_id": "p", "problem": "왜 지연이 생기나요?", "contract_version": 2},
        )
    )
    accepted = value(reply)
    await runtime.bus.drain()
    op = runtime.bus.read_operation(str(accepted["operation_id"]))
    assert op is not None
    assert op.state.value == "SUCCEEDED", op.error
    assert all(not c.context_pack.evidence for c in model.calls)
    runtime.close()
    reopened = create_runtime(
        tmp_path, model_resolver=model, resource_scope_policy=fixture_scope_policy()
    )
    try:
        status = value(
            await reopened.bus.dispatch(
                request(
                    "thread/read", "reopen", {"project_id": "p", "thread_id": accepted["thread_id"]}
                )
            )
        )
        result = status["current_result"]["result"]
        assert result["coverage"]["web_decision"] != "SKIPPED_SUFFICIENT"
        assert result["portfolio"]["hypotheses"] == []
        assert status["budget"]["calls"] > 0 and status["budget"]["actual_tokens"] is None
    finally:
        reopened.close()


@pytest.mark.asyncio
async def test_new_input_queues_after_old_attempt_and_idempotent_replay(tmp_path: Path):
    model = ControlledResearchModel(wait=True)
    runtime = await setup(tmp_path, model)
    try:
        first_request = request(
            "thread/start",
            "first",
            {"project_id": "p", "problem": "원래 질문", "contract_version": 2},
        )
        first = value(await runtime.bus.dispatch(first_request))
        try:
            await asyncio.wait_for(model.started.wait(), 10)
        except TimeoutError:
            pytest.fail(str(runtime.bus.read_operation(str(first["operation_id"]))))
        replay = value(await runtime.bus.dispatch(first_request))
        assert replay["input_id"] == first["input_id"]
        second_request = request(
            "thread/input",
            "second",
            {
                "project_id": "p",
                "thread_id": first["thread_id"],
                "contract_version": 2,
                "instruction": "조건 alpha로 한정",
            },
        )
        first_head = runtime.ledger.read_heads("p")[f"THREAD:request:{first['thread_id']}"]
        second = value(await runtime.bus.dispatch(second_request))
        assert second["status"] == "QUEUED_AFTER_CURRENT"
        assert second["request_ref"] is None
        assert runtime.ledger.read_heads("p")[f"THREAD:request:{first['thread_id']}"] == first_head
        second_replay = value(await runtime.bus.dispatch(second_request))
        assert second_replay["input_id"] == second["input_id"]
        model.release.set()
        await runtime.bus.drain()
        first_op = runtime.bus.read_operation(str(first["operation_id"]))
        second_op = runtime.bus.read_operation(str(second["operation_id"]))
        assert first_op is not None and first_op.state.value == "SUCCEEDED"
        assert second_op is not None and second_op.state.value == "SUCCEEDED"
        status = value(
            await runtime.bus.dispatch(
                request("thread/read", "read", {"project_id": "p", "thread_id": first["thread_id"]})
            )
        )
        assert status["current_result"]["operation_id"] == second["operation_id"]
        assert "원래 질문" in status["request"]["effective_question"]
        assert "조건 alpha" in status["request"]["effective_question"]
    finally:
        runtime.close()
