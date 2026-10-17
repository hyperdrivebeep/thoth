"""Read canonical research context in the caller's scope without adding transactions."""

from __future__ import annotations

from types import MappingProxyType

from thoth.application.services.action_projection import action_plan_view
from thoth.application.services.hypothesis_projection import portfolio_view
from thoth.application.services.hypothesis_test_lifecycle import HypothesisTestLifecycle
from thoth.application.services.research_basis_capture import capture_consumed
from thoth.application.services.research_currentness_view import currentness_view
from thoth.application.services.research_freshness import ResearchFreshnessService
from thoth.application.services.research_identity_contracts import ResearchContext
from thoth.application.services.research_record_persistence import decode_research
from thoth.domain.action import ActionPlan
from thoth.domain.action_full import ActionPlanRecord, ActionPortfolioRecord, ActionRecord
from thoth.domain.enums import EntityType
from thoth.domain.hypothesis import HypothesisPortfolio
from thoth.domain.hypothesis_full import HypothesisPortfolioRecord, HypothesisRecord
from thoth.domain.research_basis import BasisCurrentness
from thoth.domain.research_execution import research_work
from thoth.domain.research_identity import ResearchFamily, ResearchIdentityError
from thoth.domain.test_validity import TestValidityAssessment, hypothesis_semantic_digest
from thoth.ports.action import ActionStorePort
from thoth.ports.hypothesis import HypothesisStorePort
from thoth.ports.ledger import LedgerPort
from thoth.ports.research_identity import ResearchIdentityStorePort


def read_research_context(
    project_id: str,
    object_id: str,
    heads: dict[str, str],
    object_revision_digest: str,
    *,
    ledger: LedgerPort,
    index: ResearchIdentityStorePort,
    hypotheses_store: HypothesisStorePort,
    actions_store: ActionStorePort,
    test_lifecycle: HypothesisTestLifecycle | None,
) -> ResearchContext:
    currentness_states: dict[str, BasisCurrentness] = {}
    hypotheses: list[HypothesisRecord] = []
    capture_consumed(project_id, f"DECISION_OBJECT:{object_id}", object_revision_digest)
    freshness = ResearchFreshnessService(ledger)
    portfolios: list[HypothesisPortfolioRecord] = []
    legacy_portfolios: list[HypothesisPortfolio] = []
    actions: list[ActionRecord] = []
    action_portfolios: list[ActionPortfolioRecord] = []
    plans: list[ActionPlanRecord] = []
    legacy_plans: list[ActionPlan] = []
    test_assessments: list[TestValidityAssessment] = []
    for key, digest in sorted(heads.items()):
        if key.split(":", 1)[0] not in {EntityType.HYPOTHESIS.value, EntityType.ACTION.value}:
            continue
        revision = ledger.read_revision_by_digest(project_id, digest)
        snapshot = None if revision is None else ledger.read_snapshot(revision.snapshot_id)
        if revision is None or snapshot is None:
            raise ResearchIdentityError("RESEARCH_IDENTITY_SOURCE_MISSING")
        if key != f"{revision.entity_type.value}:{revision.entity_id}":
            raise ResearchIdentityError("RESEARCH_HEAD_IDENTITY_MISMATCH")
        decoded = decode_research(revision, snapshot)
        indexed = index.read(project_id, digest)
        if indexed is not None and indexed != decoded.identity:
            raise ResearchIdentityError("RESEARCH_IDENTITY_INDEX_MISMATCH")
        if decoded.identity.schema_family == ResearchFamily.UNRESOLVED:
            raise ResearchIdentityError("LEGACY_SCHEMA_UNRESOLVED")
        if decoded.identity.object_id != object_id:
            continue
        capture_consumed(project_id, key, digest)
        record = decoded.record
        currentness = freshness.evaluate_entity(project_id, key, digest)
        currentness_states[key] = currentness
        if record is not None:
            record = currentness_view(record, currentness)
        if isinstance(record, TestValidityAssessment) and currentness.state != "CURRENT":
            continue
        if isinstance(record, HypothesisRecord):
            hypotheses_store.read_hypothesis(project_id, record.hypothesis_id, digest)
            hypotheses.append(record)
        elif isinstance(record, HypothesisPortfolioRecord):
            hypotheses_store.read_portfolio(project_id, record.portfolio_id, digest)
            portfolios.append(record)
        elif isinstance(record, HypothesisPortfolio):
            legacy_portfolios.append(record)
        elif isinstance(record, ActionRecord):
            actions_store.read_action(project_id, record.action_id, digest)
            actions.append(record)
        elif isinstance(record, ActionPortfolioRecord):
            actions_store.read_portfolio(project_id, record.portfolio_id, digest)
            action_portfolios.append(record)
        elif isinstance(record, ActionPlanRecord):
            actions_store.read_plan(project_id, record.plan_id, digest)
            plans.append(record)
        elif isinstance(record, ActionPlan):
            legacy_plans.append(record)
        elif isinstance(record, TestValidityAssessment):
            test_assessments.append(record)
    portfolio_ids = {item.portfolio_id for item in (*hypotheses, *portfolios, *legacy_portfolios)}
    if len(portfolio_ids) > 1 or len(portfolios) + len(legacy_portfolios) > 1:
        raise ResearchIdentityError("RESEARCH_PORTFOLIO_SELECTION_REQUIRED")
    if len(plans) + len(legacy_plans) > 1:
        raise ResearchIdentityError("RESEARCH_ACTION_PLAN_SELECTION_REQUIRED")
    full = portfolios[0] if portfolios else None
    view = legacy_portfolios[0] if legacy_portfolios else None
    unavailable: tuple[str, ...] = ()
    if full is not None:
        view, unavailable = portfolio_view(full, tuple(hypotheses))
    elif hypotheses:
        unavailable = tuple(item.hypothesis_id for item in hypotheses)
    action_ids = {item.portfolio_id for item in (*actions, *action_portfolios)}
    if len(action_ids) > 1 or len(action_portfolios) > 1:
        raise ResearchIdentityError("RESEARCH_ACTION_PORTFOLIO_SELECTION_REQUIRED")
    full_plan = plans[0] if plans else None
    plan_view = legacy_plans[0] if legacy_plans else None
    if full_plan is not None:
        plan_view, missing_actions = action_plan_view(full_plan, tuple(actions))
        unavailable = (*unavailable, *missing_actions)
    tests, held_tests = _eligible_test_assessments(
        project_id,
        tuple(hypotheses),
        tuple(test_assessments),
        hypotheses_store=hypotheses_store,
        test_lifecycle=test_lifecycle,
    )
    work = research_work.get()
    if work is not None and work.request_ref.project_id == project_id:
        work.context["entity_currentness"] = {
            key: value.model_dump(mode="json") for key, value in currentness_states.items()
        }
    return ResearchContext(
        currentness=MappingProxyType(currentness_states),
        project_id=project_id,
        object_id=object_id,
        heads=MappingProxyType(dict(heads)),
        hypotheses=tuple(hypotheses),
        portfolio=full,
        portfolio_view=view,
        actions=tuple(actions),
        action_plan=full_plan,
        action_plan_view=plan_view,
        action_portfolio=action_portfolios[0] if action_portfolios else None,
        action_portfolio_id=next(iter(action_ids), None),
        unrepresented_refs=(*unavailable, *held_tests),
        portfolio_id=next(iter(portfolio_ids), None),
        test_assessments=tests,
    )


def _eligible_test_assessments(
    project_id: str,
    hypotheses: tuple[HypothesisRecord, ...],
    assessments: tuple[TestValidityAssessment, ...],
    *,
    hypotheses_store: HypothesisStorePort,
    test_lifecycle: HypothesisTestLifecycle | None,
) -> tuple[tuple[TestValidityAssessment, ...], tuple[str, ...]]:
    by_id = {item.hypothesis_id: item for item in hypotheses}
    admitted: list[TestValidityAssessment] = []
    held: list[str] = []
    for assessment in assessments:
        current = by_id.get(assessment.hypothesis_id)
        if (
            current is None
            or assessment.prediction_id not in current.prediction_refs
            or assessment.hypothesis_semantic_digest
            != hypothesis_semantic_digest(current.model_dump(mode="python"))
            or test_lifecycle is None
        ):
            held.append(assessment.assessment_id)
            continue
        test_lifecycle.require_assessment(project_id, assessment.assessment_id)
        prediction = hypotheses_store.read_prediction(assessment.prediction_id)
        if prediction is None:
            raise ResearchIdentityError("TEST_CONTEXT_PREDICTION_MISSING")
        test_lifecycle.require_prediction(prediction, current)
        admitted.append(assessment)
    return tuple(admitted), tuple(held)
