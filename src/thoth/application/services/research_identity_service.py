"""Resolve canonical research heads and stage one full Hypothesis ownership batch."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from thoth.application.services.action_projection import (
    action_plan_view,
    action_view,
    full_action,
    full_action_plan,
    full_action_portfolio,
)
from thoth.application.services.hypothesis_projection import (
    full_hypothesis,
    full_portfolio,
    portfolio_view,
)
from thoth.application.services.hypothesis_test_lifecycle import HypothesisTestLifecycle
from thoth.application.services.research_basis_capture import capture_consumed
from thoth.application.services.research_currentness_view import currentness_view
from thoth.application.services.research_freshness import ResearchFreshnessService
from thoth.application.services.research_record_persistence import (
    decode_research,
    index_revision,
    stage_full_record,
)
from thoth.application.services.revision_service import RevisionCommitService
from thoth.domain.action import ActionCandidate, ActionPlan
from thoth.domain.action_full import (
    ActionAuditRecord,
    ActionPlanRecord,
    ActionPortfolioRecord,
    ActionRecord,
)
from thoth.domain.actor import ActorRef
from thoth.domain.canonical import canonical_payload, domain_digest, head_set_digest
from thoth.domain.enums import EntityType
from thoth.domain.hypothesis import HypothesisPortfolio
from thoth.domain.hypothesis_full import (
    HypothesisAuditRecord,
    HypothesisPortfolioRecord,
    HypothesisRecord,
    PredictionRecord,
)
from thoth.domain.research_basis import BasisCurrentness
from thoth.domain.research_execution import research_work
from thoth.domain.research_identity import ResearchFamily, ResearchIdentity, ResearchIdentityError
from thoth.domain.revision import ImpactPropagationPlan, RevisionChangeSet, StagedRevision
from thoth.domain.sandbox import SandboxRunSpec
from thoth.domain.test_validity import TestValidityAssessment, hypothesis_semantic_digest
from thoth.ports.action import ActionStorePort
from thoth.ports.decision_object import DecisionObjectStorePort
from thoth.ports.hypothesis import HypothesisStorePort
from thoth.ports.ledger import LedgerPort
from thoth.ports.research_identity import ResearchIdentityStorePort
from thoth.ports.runtime import ClockPort, IdGeneratorPort


@dataclass(frozen=True)
class ResearchContext:
    project_id: str
    object_id: str
    heads: Mapping[str, str]
    hypotheses: tuple[HypothesisRecord, ...] = ()
    portfolio: HypothesisPortfolioRecord | None = None
    portfolio_view: HypothesisPortfolio | None = None
    actions: tuple[ActionRecord, ...] = ()
    action_plan: ActionPlanRecord | None = None
    action_plan_view: ActionPlan | None = None
    action_portfolio: ActionPortfolioRecord | None = None
    action_portfolio_id: str | None = None
    unrepresented_refs: tuple[str, ...] = ()
    portfolio_id: str | None = None
    test_assessments: tuple[TestValidityAssessment, ...] = ()
    currentness: Mapping[str, BasisCurrentness] = field(default_factory=dict[str, BasisCurrentness])


@dataclass(frozen=True)
class HypothesisOwnershipBatch:
    hypotheses: tuple[HypothesisRecord, ...]
    portfolio: HypothesisPortfolioRecord
    staged: tuple[StagedRevision, ...]


@dataclass(frozen=True)
class ActionOwnershipBatch:
    actions: tuple[ActionRecord, ...]
    portfolio: ActionPortfolioRecord
    plan: ActionPlanRecord
    staged: tuple[StagedRevision, ...]


@dataclass(frozen=True)
class ResearchOwnershipBatch:
    hypotheses: HypothesisOwnershipBatch
    actions: ActionOwnershipBatch

    @property
    def additional_staged(self) -> tuple[StagedRevision, ...]:
        return (*self.hypotheses.staged[1:], *self.actions.staged[1:])


class ResearchIdentityService:
    def __init__(
        self,
        *,
        ledger: LedgerPort,
        index: ResearchIdentityStorePort,
        hypotheses: HypothesisStorePort,
        actions: ActionStorePort,
        objects: DecisionObjectStorePort,
        clock: ClockPort,
        ids: IdGeneratorPort,
        test_lifecycle: HypothesisTestLifecycle | None = None,
    ) -> None:
        self._ledger = ledger
        self._index = index
        self._hypotheses = hypotheses
        self._actions = actions
        self._objects = objects
        self._clock = clock
        self._ids = ids
        self._test_lifecycle = test_lifecycle

    def context(self, project_id: str, object_id: str, heads: dict[str, str]) -> ResearchContext:
        object_record = self._objects.read_object(project_id, object_id, None)
        if (
            object_record is None
            or heads.get(f"DECISION_OBJECT:{object_id}") != object_record.revision_digest
        ):
            raise ResearchIdentityError("RESEARCH_OBJECT_HEAD_MISMATCH")
        currentness_states: dict[str, BasisCurrentness] = {}
        hypotheses: list[HypothesisRecord] = []
        capture_consumed(project_id, f"DECISION_OBJECT:{object_id}", object_record.revision_digest)
        freshness = ResearchFreshnessService(self._ledger)
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
            revision = self._ledger.read_revision_by_digest(project_id, digest)
            snapshot = (
                None if revision is None else self._ledger.read_snapshot(revision.snapshot_id)
            )
            if revision is None or snapshot is None:
                raise ResearchIdentityError("RESEARCH_IDENTITY_SOURCE_MISSING")
            if key != f"{revision.entity_type.value}:{revision.entity_id}":
                raise ResearchIdentityError("RESEARCH_HEAD_IDENTITY_MISMATCH")
            decoded = decode_research(revision, snapshot)
            indexed = self._index.read(project_id, digest)
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
                self._hypotheses.read_hypothesis(project_id, record.hypothesis_id, digest)
                hypotheses.append(record)
            elif isinstance(record, HypothesisPortfolioRecord):
                self._hypotheses.read_portfolio(project_id, record.portfolio_id, digest)
                portfolios.append(record)
            elif isinstance(record, HypothesisPortfolio):
                legacy_portfolios.append(record)
            elif isinstance(record, ActionRecord):
                self._actions.read_action(project_id, record.action_id, digest)
                actions.append(record)
            elif isinstance(record, ActionPortfolioRecord):
                self._actions.read_portfolio(project_id, record.portfolio_id, digest)
                action_portfolios.append(record)
            elif isinstance(record, ActionPlanRecord):
                self._actions.read_plan(project_id, record.plan_id, digest)
                plans.append(record)
            elif isinstance(record, ActionPlan):
                legacy_plans.append(record)
            elif isinstance(record, TestValidityAssessment):
                test_assessments.append(record)
        portfolio_ids = {
            item.portfolio_id for item in (*hypotheses, *portfolios, *legacy_portfolios)
        }
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
        tests, held_tests = self._eligible_test_assessments(
            project_id, tuple(hypotheses), tuple(test_assessments)
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
        self,
        project_id: str,
        hypotheses: tuple[HypothesisRecord, ...],
        assessments: tuple[TestValidityAssessment, ...],
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
                or self._test_lifecycle is None
            ):
                held.append(assessment.assessment_id)
                continue
            self._test_lifecycle.require_assessment(project_id, assessment.assessment_id)
            prediction = self._hypotheses.read_prediction(assessment.prediction_id)
            if prediction is None:
                raise ResearchIdentityError("TEST_CONTEXT_PREDICTION_MISSING")
            self._test_lifecycle.require_prediction(prediction, current)
            admitted.append(assessment)
        return tuple(admitted), tuple(held)

    def _require_identity(
        self,
        project_id: str,
        object_id: str,
        identifier: str,
        family: ResearchFamily,
    ) -> None:
        kind = (
            EntityType.HYPOTHESIS
            if family in {ResearchFamily.HYPOTHESIS, ResearchFamily.HYPOTHESIS_PORTFOLIO}
            else EntityType.ACTION
        )
        known = list(self._index.find_entity(identifier, kind))
        # Unindexed legacy/direct writers do not create a hole in scope validation.
        for revision, snapshot in self._index.unindexed_revisions():
            if revision.entity_type == kind:
                identity = decode_research(revision, snapshot).identity
                if identity.entity_id == identifier or identifier in identity.embedded_entity_ids:
                    known.append(identity)
        allowed = {family}
        if family == ResearchFamily.HYPOTHESIS_PORTFOLIO:
            allowed.add(ResearchFamily.LEGACY_HYPOTHESIS_PORTFOLIO)
        if family == ResearchFamily.ACTION_PLAN:
            allowed.add(ResearchFamily.LEGACY_ACTION_PLAN)

        def same_family(item: ResearchIdentity) -> bool:
            if item.entity_id == identifier:
                return item.schema_family in allowed
            return identifier in item.embedded_entity_ids and (
                (
                    family == ResearchFamily.HYPOTHESIS
                    and item.schema_family == ResearchFamily.LEGACY_HYPOTHESIS_PORTFOLIO
                )
                or (
                    family == ResearchFamily.ACTION
                    and item.schema_family == ResearchFamily.LEGACY_ACTION_PLAN
                )
            )

        if any(
            item.project_id != project_id or item.object_id != object_id or not same_family(item)
            for item in known
        ):
            raise ResearchIdentityError("RESEARCH_ENTITY_SCOPE_OR_FAMILY_MISMATCH")

    def read_portfolio(self, project_id: str, portfolio_id: str) -> HypothesisPortfolio:
        heads = dict(self._ledger.read_heads(project_id))
        digest = heads.get(f"HYPOTHESIS:{portfolio_id}")
        revision = (
            None if digest is None else self._ledger.read_revision_by_digest(project_id, digest)
        )
        snapshot = None if revision is None else self._ledger.read_snapshot(revision.snapshot_id)
        if revision is None or snapshot is None:
            raise ResearchIdentityError("RESEARCH_IDENTITY_SOURCE_MISSING")
        decoded = decode_research(revision, snapshot)
        if isinstance(decoded.record, HypothesisPortfolio):
            return decoded.record
        if not isinstance(decoded.record, HypothesisPortfolioRecord):
            raise ResearchIdentityError("RESEARCH_PORTFOLIO_FAMILY_MISMATCH")
        records: list[HypothesisRecord] = []
        for identifier in decoded.record.hypothesis_refs:
            head = heads.get(f"HYPOTHESIS:{identifier}")
            member = (
                None if head is None else self._ledger.read_revision_by_digest(project_id, head)
            )
            content = None if member is None else self._ledger.read_snapshot(member.snapshot_id)
            if member is None or content is None:
                raise ResearchIdentityError("RESEARCH_IDENTITY_SOURCE_MISSING")
            value = decode_research(member, content).record
            if (
                not isinstance(value, HypothesisRecord)
                or value.object_id != decoded.record.object_id
            ):
                raise ResearchIdentityError("RESEARCH_MEMBER_SCOPE_MISMATCH")
            records.append(value)
        view, _unrepresented = portfolio_view(decoded.record, tuple(records))
        if view is None:
            raise ResearchIdentityError("RESEARCH_PORTFOLIO_VIEW_UNAVAILABLE")
        return view

    def read_action_plan(self, project_id: str, plan_id: str) -> ActionPlan:
        plan = self._actions.read_plan(project_id, plan_id, None)
        if plan is None:
            raise ResearchIdentityError("RESEARCH_IDENTITY_SOURCE_MISSING")
        view, _unrepresented = action_plan_view(plan, self._actions.list_actions(project_id))
        if view is None:
            raise ResearchIdentityError("RESEARCH_ACTION_PLAN_VIEW_UNAVAILABLE")
        return view

    def stage_hypotheses(
        self,
        candidate: HypothesisPortfolio,
        *,
        actor: ActorRef,
        context: ResearchContext,
    ) -> HypothesisOwnershipBatch:
        project_id, object_id, heads = context.project_id, context.object_id, context.heads
        if candidate.object_id != object_id or any(
            item.object_id != object_id for item in candidate.hypotheses
        ):
            raise ResearchIdentityError("RESEARCH_CANDIDATE_OBJECT_MISMATCH")
        ids = [item.hypothesis_id for item in candidate.hypotheses]
        if len(ids) != len(set(ids)) or candidate.portfolio_id in ids:
            raise ResearchIdentityError("RESEARCH_DUPLICATE_CANDIDATE_ID")
        if context.portfolio_id is not None and context.portfolio_id != candidate.portfolio_id:
            raise ResearchIdentityError("RESEARCH_PORTFOLIO_ID_CHANGED")
        self._require_identity(
            project_id, object_id, candidate.portfolio_id, ResearchFamily.HYPOTHESIS_PORTFOLIO
        )
        current = {item.hypothesis_id: item for item in context.hypotheses}
        records: list[HypothesisRecord] = []
        for item in candidate.hypotheses:
            self._require_identity(
                project_id, object_id, item.hypothesis_id, ResearchFamily.HYPOTHESIS
            )
            records.append(
                full_hypothesis(
                    item,
                    project_id=project_id,
                    portfolio_id=candidate.portfolio_id,
                    revision_id=self._ids.new("hypothesis-revision"),
                    created_at=self._clock.now(),
                    parent=heads.get(f"HYPOTHESIS:{item.hypothesis_id}"),
                    current=current.get(item.hypothesis_id),
                )
            )
        retained = (
            context.portfolio.hypothesis_refs if context.portfolio is not None else tuple(current)
        )
        full = full_portfolio(
            candidate,
            project_id=project_id,
            revision_id=self._ids.new("portfolio-revision"),
            created_at=self._clock.now(),
            parent=heads.get(f"HYPOTHESIS:{candidate.portfolio_id}"),
            retained_refs=retained,
            current=context.portfolio,
        )
        staged = tuple(
            stage_full_record(
                record, ids=self._ids, actor=actor, reason="normal research candidate admission"
            )
            for record in (full, *records)
        )
        return HypothesisOwnershipBatch(tuple(records), full, staged)

    def persist_hypotheses(self, batch: HypothesisOwnershipBatch) -> None:
        """Caller owns the encompassing ledger/UoW transaction and successful head transition."""
        for record in batch.hypotheses:
            self._hypotheses.add_hypothesis(record)
        self._hypotheses.add_portfolio(batch.portfolio)
        for item in batch.staged:
            index_revision(
                self._ledger, self._index, item.revision.project_id, item.revision.revision_digest
            )
            payload: dict[str, object] = {
                "revision_digest": item.revision.revision_digest,
                "semantic_truth_certified": False,
            }
            draft: dict[str, object] = {
                "audit_id": self._ids.new("hypothesis-audit"),
                "project_id": item.revision.project_id,
                "hypothesis_id": item.revision.entity_id,
                "event_type": "hypothesis/canonicalProjectionUpdated",
                "payload": payload,
                "created_at": self._clock.now(),
            }
            self._hypotheses.append_audit(
                HypothesisAuditRecord.model_validate(
                    {
                        **draft,
                        "event_digest": domain_digest(
                            "HYPOTHESIS_AUDIT", "1.0.0", canonical_payload(draft)
                        ),
                    }
                )
            )

    def stage_actions(
        self,
        candidate: ActionPlan,
        *,
        actor: ActorRef,
        context: ResearchContext,
        hypotheses: tuple[HypothesisRecord, ...],
    ) -> ActionOwnershipBatch:
        project, object_id, heads = context.project_id, context.object_id, context.heads
        ids = [item.action_id for item in candidate.alternatives]
        if candidate.object_id != object_id or any(
            item.object_id != object_id for item in candidate.alternatives
        ):
            raise ResearchIdentityError("RESEARCH_CANDIDATE_OBJECT_MISMATCH")
        if len(ids) != len(set(ids)) or candidate.plan_id in ids:
            raise ResearchIdentityError("RESEARCH_DUPLICATE_CANDIDATE_ID")
        previous_id = (
            context.action_plan.plan_id
            if context.action_plan
            else context.action_plan_view.plan_id
            if context.action_plan_view
            else None
        )
        if previous_id is not None and previous_id != candidate.plan_id:
            raise ResearchIdentityError("RESEARCH_ACTION_PLAN_ID_CHANGED")
        hypothesis_ids = {item.hypothesis_id for item in (*context.hypotheses, *hypotheses)}
        if any(
            not set(item.hypothesis_ids).issubset(hypothesis_ids) for item in candidate.alternatives
        ):
            raise ResearchIdentityError("RESEARCH_ACTION_HYPOTHESIS_SCOPE_MISMATCH")
        portfolio_id = context.action_portfolio_id or self._ids.new("action-portfolio")
        if portfolio_id in {*ids, candidate.plan_id}:
            raise ResearchIdentityError("RESEARCH_DUPLICATE_CANDIDATE_ID")
        self._require_identity(project, object_id, candidate.plan_id, ResearchFamily.ACTION_PLAN)
        self._require_identity(project, object_id, portfolio_id, ResearchFamily.ACTION_PORTFOLIO)
        current = {item.action_id: item for item in context.actions}
        records: list[ActionRecord] = []
        for item in candidate.alternatives:
            self._require_identity(project, object_id, item.action_id, ResearchFamily.ACTION)
            records.append(
                full_action(
                    item,
                    project_id=project,
                    portfolio_id=portfolio_id,
                    revision_id=self._ids.new("action-revision"),
                    created_at=self._clock.now(),
                    parent=heads.get(f"ACTION:{item.action_id}"),
                    current=current.get(item.action_id),
                )
            )
        portfolio = full_action_portfolio(
            candidate,
            project_id=project,
            portfolio_id=portfolio_id,
            revision_id=self._ids.new("action-portfolio-revision"),
            created_at=self._clock.now(),
            parent=heads.get(f"ACTION:{portfolio_id}"),
            retained_refs=tuple(current),
            current=context.action_portfolio,
        )
        plan = full_action_plan(
            candidate,
            project_id=project,
            portfolio_id=portfolio_id,
            revision_id=self._ids.new("action-plan-revision"),
            created_at=self._clock.now(),
            parent=heads.get(f"ACTION:{candidate.plan_id}"),
            actions=tuple(records),
            current=context.action_plan,
        )
        staged = tuple(
            stage_full_record(
                record, ids=self._ids, actor=actor, reason="normal action candidate admission"
            )
            for record in (plan, portfolio, *records)
        )
        return ActionOwnershipBatch(tuple(records), portfolio, plan, staged)

    def stage_cycle(
        self,
        portfolio: HypothesisPortfolio,
        plan: ActionPlan,
        *,
        actor: ActorRef,
        context: ResearchContext,
    ) -> ResearchOwnershipBatch:
        hypotheses = self.stage_hypotheses(portfolio, actor=actor, context=context)
        actions = self.stage_actions(
            plan, actor=actor, context=context, hypotheses=hypotheses.hypotheses
        )
        return ResearchOwnershipBatch(hypotheses, actions)

    def bind_execution_plan(
        self,
        context: ResearchContext,
        action: ActionCandidate,
        spec: SandboxRunSpec,
        prediction_refs: tuple[str, ...],
        actor: ActorRef,
    ) -> ActionPlanRecord:
        current = context.action_plan
        if (
            current is None
            or spec.action_plan_id != current.plan_id
            or spec.action_id != action.action_id
        ):
            raise ResearchIdentityError("TEST_EXECUTION_PLAN_MISMATCH")
        for reference in prediction_refs:
            digest = context.heads.get(f"HYPOTHESIS:{reference}")
            revision = (
                None
                if digest is None
                else self._ledger.read_revision_by_digest(context.project_id, digest)
            )
            snapshot = (
                None if revision is None else self._ledger.read_snapshot(revision.snapshot_id)
            )
            if revision is None or snapshot is None:
                raise ResearchIdentityError("TEST_PLAN_PREDICTION_NOT_SEALED")
            prediction = decode_research(revision, snapshot).record
            if (
                not isinstance(prediction, PredictionRecord)
                or prediction.object_id != context.object_id
                or prediction.hypothesis_id not in action.hypothesis_ids
            ):
                raise ResearchIdentityError("TEST_PLAN_PREDICTION_SCOPE_MISMATCH")
        steps = self._bound_execution_steps(context, action, spec, prediction_refs)
        prior_frontier = set(current.auto_executable_frontier)
        frontier = tuple(
            str(step["step_id"])
            for step in steps
            if step["step_id"] in prior_frontier or step["action_id"] in prior_frontier
        )
        draft = current.model_dump(mode="python")
        draft.update(
            {
                "plan_revision_id": self._ids.new("action-plan-revision"),
                "steps": steps,
                "auto_executable_frontier": frontier,
                "authorization_refs": (),
                "validation_state": "EXECUTION_BOUND",
                "freshness": "CURRENT",
                "supersedes_revision_digest": current.revision_digest,
                "created_at": self._clock.now(),
                "revision_digest": "0" * 64,
            }
        )
        plan = ActionPlanRecord.model_validate(draft)
        plan = plan.model_copy(
            update={
                "revision_digest": domain_digest(
                    "ACTION_PLAN",
                    "2.0.0",
                    canonical_payload(plan.model_dump(mode="python", exclude={"revision_digest"})),
                )
            }
        )
        staged = stage_full_record(
            plan, ids=self._ids, actor=actor, reason="bind selected research execution inputs"
        )
        with self._ledger.transaction():
            if dict(context.heads) != dict(self._ledger.read_heads(context.project_id)):
                raise ResearchIdentityError("TEST_EXECUTION_PLAN_BASIS_CHANGED")
            commit = RevisionCommitService(
                self._ledger, self._clock, self._ids, policy_version=spec.policy_digest
            ).commit(
                RevisionChangeSet(
                    changeset_id=self._ids.new("changeset"),
                    project_id=context.project_id,
                    expected_heads={f"ACTION:{plan.plan_id}": current.revision_digest},
                    expected_head_set_digest=head_set_digest(dict(context.heads)),
                    staged_revisions=(staged,),
                    impact_plan=ImpactPropagationPlan(),
                    actor=actor,
                    reason="bind selected research execution inputs",
                )
            )
            if not commit.committed_revision_ids:
                raise ResearchIdentityError("TEST_EXECUTION_PLAN_CONFLICT")
            self._actions.add_plan(plan)
            payload: dict[str, object] = {
                "revision_digest": plan.revision_digest,
                "prediction_refs": prediction_refs,
                "semantic_truth_certified": False,
            }
            audit = {
                "project_id": context.project_id,
                "subject_id": plan.plan_id,
                "event_type": "action/researchExecutionBound",
                "payload": payload,
                "created_at": self._clock.now(),
            }
            self._actions.append_audit(
                ActionAuditRecord.model_validate(
                    {
                        **audit,
                        "audit_id": self._ids.new("action-audit"),
                        "event_digest": domain_digest(
                            "ACTION_AUDIT", "1.0.0", canonical_payload(audit)
                        ),
                    }
                )
            )
        return plan

    @staticmethod
    def _bound_execution_steps(
        context: ResearchContext,
        action: ActionCandidate,
        spec: SandboxRunSpec,
        prediction_refs: tuple[str, ...],
    ) -> tuple[dict[str, object], ...]:
        current = context.action_plan
        assert current is not None
        by_id = {item.action_id: item for item in context.actions}
        selected = by_id.get(action.action_id)
        if (
            selected is None
            or action_view(selected) != action
            or selected.risk_tier != "R2"
            or not action.effect_completeness_confirmed
        ):
            raise ResearchIdentityError("TEST_EXECUTION_ACTION_BASIS_MISMATCH")
        steps: list[dict[str, object]] = []
        found = False
        for prior in current.steps:
            identifier = prior.get("action_id")
            record = by_id.get(identifier) if isinstance(identifier, str) else None
            if record is None:
                raise ResearchIdentityError("TEST_EXECUTION_STEP_IDENTITY_UNRESOLVED")
            step = {
                **prior,
                "action_revision_digest": record.revision_digest,
                "risk_tier": record.risk_tier,
                "effect_vector": record.effect_vector,
                "required_roles": record.required_roles,
                "required_processes": record.required_processes,
                "policy_state": record.policy_state,
            }
            if identifier == action.action_id:
                if found or prior.get("state", "READY") != "READY":
                    raise ResearchIdentityError("TEST_EXECUTION_STEP_NOT_READY")
                found = True
                step.update(
                    {
                        "input_digests": tuple(
                            item.content_sha256 for item in spec.input_snapshots
                        ),
                        "prediction_refs": prediction_refs,
                        "hypothesis_refs": action.hypothesis_ids,
                        "runtime_binding": {
                            "runtime_profile": spec.runtime_profile.value,
                            "image_digest": spec.image_digest,
                            "argv": spec.argv,
                            "policy_digest": spec.policy_digest,
                        },
                        "policy_state": "PREAUTHORIZED",
                        "state": "READY",
                    }
                )
            steps.append(step)
        if not found:
            raise ResearchIdentityError("TEST_EXECUTION_STEP_NOT_IN_PLAN")
        return tuple(steps)

    def persist_actions(self, batch: ActionOwnershipBatch) -> None:
        for record in batch.actions:
            self._actions.add_action(record)
        self._actions.add_portfolio(batch.portfolio)
        self._actions.add_plan(batch.plan)
        for item in batch.staged:
            index_revision(
                self._ledger, self._index, item.revision.project_id, item.revision.revision_digest
            )
            draft: dict[str, object] = {
                "audit_id": self._ids.new("action-audit"),
                "project_id": item.revision.project_id,
                "subject_id": item.revision.entity_id,
                "event_type": "action/canonicalProjectionUpdated",
                "payload": {
                    "revision_digest": item.revision.revision_digest,
                    "semantic_truth_certified": False,
                },
                "created_at": self._clock.now(),
            }
            self._actions.append_audit(
                ActionAuditRecord.model_validate(
                    {
                        **draft,
                        "event_digest": domain_digest(
                            "ACTION_AUDIT", "1.0.0", canonical_payload(draft)
                        ),
                    }
                )
            )

    def persist_cycle(self, batch: ResearchOwnershipBatch) -> None:
        self.persist_hypotheses(batch.hypotheses)
        self.persist_actions(batch.actions)
        project = batch.hypotheses.portfolio.project_id
        for key, digest in self._ledger.read_heads(project).items():
            if key.split(":", 1)[0] in {"HYPOTHESIS", "ACTION", "CRITERION", "OUTCOME"}:
                index_revision(self._ledger, self._index, project, digest)
