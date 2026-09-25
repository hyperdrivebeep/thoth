from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from thoth.adapters.models import ScriptedModel
from thoth.adapters.parsers.registry import default_parser_registry
from thoth.adapters.storage import (
    ContentAddressedObjectStore,
    SqliteArtifactLedger,
    SqliteDecisionObjectStore,
    SqliteDependencyGraph,
    SqliteLedger,
    SqliteMemoryStore,
    SqliteProjectStore,
    migrate_sqlite_database,
)
from thoth.adapters.storage.bundle import SqliteStoreBundle
from thoth.application.reducers import SufficiencySignals
from thoth.application.services import (
    DecisionObjectService,
    IngestArtifactCommand,
    IngestionService,
    RevisionCommitService,
)
from thoth.application.workflows import ThreadCycleCommand, ThreadCycleService
from thoth.apps.research_runtime import create_research_components
from thoth.domain.action import (
    ActionCompilationPolicy,
    ActionDraft,
    ActionPlanDraft,
    ActionRiskFacts,
    DecisionAnalysis,
    DecisionCriterion,
)
from thoth.domain.actor import ActorRef
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.enums import (
    ActorKind,
    AuthorityState,
    CausalDepth,
    CausalLocus,
    CutoffState,
    HypothesisStatus,
    PortfolioStatus,
    Reversibility,
    RiskTier,
    SecurityClass,
)
from thoth.domain.hypothesis import DiscriminatingTest, Hypothesis, HypothesisPortfolio
from thoth.domain.project import Project


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 8, 30, 7, 10, tzinfo=UTC)


class SequenceIds:
    def __init__(self) -> None:
        self._next = 0

    def new(self, prefix: str) -> str:
        self._next += 1
        return f"{prefix}:{self._next}"


def _hypothesis(identifier: str, locus: CausalLocus, span_id: str) -> Hypothesis:
    return Hypothesis(
        hypothesis_id=identifier,
        object_id="object:cycle",
        statement=f"{locus.value} may explain the blocked result",
        observed_problem="result is below the planned target",
        primary_locus=locus,
        causal_depth=CausalDepth.INTERMEDIATE,
        scope_conditions={"scope": "fixture"},
        support_evidence_refs=(span_id,),
        counterevidence_refs=(),
        counterevidence_queries=(f"search evidence against {locus.value}",),
        assumptions=("source locator is correct",),
        uncertainty="cause is not isolated",
        predicted_observations=("a discriminating comparison changes the mismatch",),
        discriminating_tests=(
            DiscriminatingTest(
                test_id=f"test:{identifier}",
                procedure_candidate="compare aligned and current inputs",
                expected_if_true="mismatch narrows",
                expected_if_alternative="mismatch remains",
                risk_tier=RiskTier.R1,
                reversibility=Reversibility.FULL,
            ),
        ),
        status=HypothesisStatus.TESTABLE,
    )


@pytest.mark.asyncio
async def test_scripted_two_document_cycle_commits_receipt_and_memory(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    migrate_sqlite_database(workspace / "db" / "thoth.sqlite3")
    ledger = SqliteLedger(workspace / "db" / "thoth.sqlite3")
    ledger.initialize()
    clock = FixedClock()
    ids = SequenceIds()
    projects = SqliteProjectStore(ledger.engine)
    projects.create(
        Project(
            project_id="project:cycle",
            name="Generic integration fixture",
            cutoff_at=clock.now(),
            overlay="general-rnd",
            policy_binding_ref="policy:default",
        ),
        created_at=clock.now().isoformat(),
    )
    artifact_ledger = SqliteArtifactLedger(ledger.engine)
    ingestion = IngestionService(
        projects=projects,
        objects=ContentAddressedObjectStore(workspace),
        parsers=default_parser_registry(),
        artifacts=artifact_ledger,
        clock=clock,
        ids=ids,
    )
    plan_doc = ingestion.ingest(
        IngestArtifactCommand(
            project_id="project:cycle",
            source_uri="sources/plan.md",
            media_type="text/markdown",
            raw=b"# Plan\nTarget condition A must be met.\n",
            authority=AuthorityState.OFFICIAL,
            cutoff_state=CutoffState.ELIGIBLE,
            security_class=SecurityClass.INTERNAL,
            operation_id="operation:plan",
            source_path=Path("plan.md"),
        )
    )
    report_doc = ingestion.ingest(
        IngestArtifactCommand(
            project_id="project:cycle",
            source_uri="sources/report.md",
            media_type="text/markdown",
            raw=b"# Report\nObserved result differs under condition B.\n",
            authority=AuthorityState.OFFICIAL,
            cutoff_state=CutoffState.ELIGIBLE,
            security_class=SecurityClass.INTERNAL,
            operation_id="operation:report",
            source_path=Path("report.md"),
        )
    )
    evidence = plan_doc.evidence_candidates + report_doc.evidence_candidates
    object_store = SqliteDecisionObjectStore(ledger.engine)
    object_service = DecisionObjectService(
        store=object_store,
        artifacts=artifact_ledger,
        dependencies=SqliteDependencyGraph(ledger.engine),
        ledger=ledger,
        commits=RevisionCommitService(ledger, clock, ids, policy_version="policy:default"),
        clock=clock,
        ids=ids,
    )
    object_service.seed_profiles()
    _candidate, materialized, _commit = object_service.materialize(
        project_id="project:cycle",
        thread_id="thread:cycle",
        object_id="object:cycle",
        purpose_statement="Compare conditions",
        problem_frame="Result differs from plan",
        focus_refs=("fixture:cycle",),
        trigger_evidence_refs=tuple(span.span_id for span in evidence),
        profile_refs=("GENERAL_RND_DECISION",),
        actor_ref="agent:cycle",
        trigger_type="PROJECTPACK_SCENARIO",
    )
    assert materialized is not None
    span_id = evidence[0].span_id
    expected_head = domain_digest(
        "WORKING_HEADS", "1.0.0", canonical_payload(ledger.read_heads("project:cycle"))
    )
    portfolio = HypothesisPortfolio(
        portfolio_id="portfolio:cycle",
        object_id="object:cycle",
        hypotheses=(
            _hypothesis("hypothesis:input", CausalLocus.INPUT_MATERIAL_DATA, span_id),
            _hypothesis("hypothesis:method", CausalLocus.METHOD_DESIGN_IMPLEMENTATION, span_id),
            _hypothesis("hypothesis:unknown", CausalLocus.OTHER_WITH_DESCRIPTION, span_id),
        ),
        status=PortfolioStatus.TESTABLE,
        generated_from_head_set=expected_head,
    )
    actions = (
        ActionDraft(
            action_id="action:compare",
            object_id="object:cycle",
            hypothesis_ids=("hypothesis:input",),
            action_family="READ_ONLY_COMPARISON",
            specification="compare source and run identifiers",
            expected_information_value="tests input mismatch",
            reversibility=Reversibility.FULL,
            effect_facts=ActionRiskFacts(),
            effect_completeness_confirmed=True,
            source_refs=(span_id,),
        ),
        ActionDraft(
            action_id="action:replay",
            object_id="object:cycle",
            hypothesis_ids=("hypothesis:method",),
            action_family="SANDBOX_REPLAY",
            specification="replay the evaluator in an isolated fixture",
            expected_information_value="tests method mismatch",
            reversibility=Reversibility.FULL,
            effect_facts=ActionRiskFacts(runs_untrusted_code=True),
            effect_completeness_confirmed=True,
            source_refs=(span_id,),
        ),
    )
    action_plan = ActionPlanDraft(
        plan_id="plan:cycle",
        object_id="object:cycle",
        alternatives=actions,
        decision_analysis=DecisionAnalysis(
            decision="choose the next discriminating check",
            criteria=(
                DecisionCriterion(
                    criterion_id="criterion:information",
                    name="information gain",
                    mandatory=True,
                    rationale="separate competing hypotheses",
                ),
            ),
            evaluations=(),
            uncertainty="official evaluator criterion is not yet bound",
            sensitivity="field access is excluded from this fixture",
        ),
        proposed_frontier=("action:compare", "action:replay"),
        plan_revision_digest=expected_head,
    )
    rejected_action_plan = action_plan.model_copy(
        update={"proposed_frontier": ("action:not-in-plan",)}
    )
    model = ScriptedModel(
        {
            (
                "case:cycle",
                "HYPOTHESIS_GENERATOR",
                "hypothesis_portfolio.v2",
            ): portfolio.model_dump(mode="python"),
            (
                "case:cycle",
                "ACTION_PLANNER",
                "action_alternatives.v2",
            ): rejected_action_plan.model_dump(mode="python"),
            (
                "case:cycle",
                "ACTION_PLANNER",
                "action_alternatives.semantic_repair.v2",
            ): action_plan.model_dump(mode="python"),
        }
    )
    memory = SqliteMemoryStore(ledger.engine)
    commits = RevisionCommitService(ledger, clock, ids, policy_version="policy:default")
    components = create_research_components(
        stores=SqliteStoreBundle(ledger, tmp_path),
        ledger=ledger,
        objects=object_store,
        artifacts=artifact_ledger,
        clock=clock,
        ids=ids,
    )
    cycle = ThreadCycleService(
        research=components.identity,
        ledger=ledger,
        memory=memory,
        model=model,
        commits=commits,
        clock=clock,
        ids=ids,
        dependencies=SqliteDependencyGraph(ledger.engine),
    )

    result = await cycle.execute(
        ThreadCycleCommand(
            case_id="case:cycle",
            project_id="project:cycle",
            thread_id="thread:cycle",
            object_id="object:cycle",
            problem="Why does the reported condition differ from the plan?",
            cutoff_at=clock.now(),
            criteria=(),
            evidence=evidence,
            sufficiency_signals=SufficiencySignals(),
            action_policy=ActionCompilationPolicy(
                minimum_tier_by_family={
                    "READ_ONLY_COMPARISON": RiskTier.R0,
                    "SANDBOX_REPLAY": RiskTier.R2,
                }
            ),
            actor=ActorRef(actor_id="agent:cycle", kind=ActorKind.AGENT, role="cycle-runner"),
            policy_version="policy:default",
            model_policy_ref="model-policy:scripted",
        )
    )

    assert len(result.commit.committed_revision_ids) == 4 + len(portfolio.hypotheses) + len(actions)
    assert result.commit.receipt.semantic_truth_certified is False
    assert result.model_ids == ("SCRIPTED_MODEL", "SCRIPTED_MODEL")
    assert result.semantic_repair_attempted is True
    assert len(result.memory_ids) == 3 + len(portfolio.hypotheses) + len(actions)
    assert len(memory.list("project:cycle")) == 3 + len(portfolio.hypotheses) + len(actions)
    full_plan = components.actions.read_plan("project:cycle", "plan:cycle", None)
    assert full_plan is not None and full_plan.generation_details is not None
    assert set(ledger.read_heads("project:cycle")) == {
        "DECISION_OBJECT:object:cycle",
        f"EVIDENCE:{result.assessment.assessment_id}",
        "HYPOTHESIS:portfolio:cycle",
        "ACTION:plan:cycle",
        f"ACTION:{full_plan.generation_details.portfolio_id}",
        *(f"ACTION:{item.action_id}" for item in actions),
        *(f"HYPOTHESIS:{item.hypothesis_id}" for item in portfolio.hypotheses),
    }
    ledger.close()
