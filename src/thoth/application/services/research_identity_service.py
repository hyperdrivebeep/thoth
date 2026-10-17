"""Resolve canonical research heads and stage one full Hypothesis ownership batch."""

from __future__ import annotations

from thoth.application.services.action_projection import (
    action_plan_view,
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
from thoth.application.services.research_commit_scope import read_scope
from thoth.application.services.research_context_read import read_research_context
from thoth.application.services.research_execution_steps import (
    bound_execution_steps as _build_bound_execution_steps,
)
from thoth.application.services.research_identity_contracts import (
    ActionOwnershipBatch,
)
from thoth.application.services.research_identity_contracts import (
    HypothesisOwnershipBatch as HypothesisOwnershipBatch,
)
from thoth.application.services.research_identity_contracts import (
    ResearchContext as ResearchContext,
)
from thoth.application.services.research_identity_contracts import (
    ResearchOwnershipBatch as ResearchOwnershipBatch,
)
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
from thoth.domain.research_execution import research_work
from thoth.domain.research_identity import ResearchFamily, ResearchIdentity, ResearchIdentityError
from thoth.domain.revision import ImpactPropagationPlan, RevisionChangeSet
from thoth.domain.sandbox import SandboxRunSpec
from thoth.ports.action import ActionStorePort
from thoth.ports.decision_object import DecisionObjectStorePort
from thoth.ports.hypothesis import HypothesisStorePort
from thoth.ports.ledger import LedgerPort
from thoth.ports.research_identity import ResearchIdentityStorePort
from thoth.ports.runtime import ClockPort, IdGeneratorPort


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
        return read_research_context(
            project_id,
            object_id,
            heads,
            object_record.revision_digest,
            ledger=self._ledger,
            index=self._index,
            hypotheses_store=self._hypotheses,
            actions_store=self._actions,
            test_lifecycle=self._test_lifecycle,
        )

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
            plan_key = f"ACTION:{plan.plan_id}"
            scope = read_scope(research_work.get(), context.project_id, context.heads, (plan_key,))
            current_heads = self._ledger.read_heads(context.project_id)
            if scope is None:
                changed = dict(context.heads) != dict(current_heads)
            else:
                changed = any(
                    current_heads.get(key) != digest for key, digest in scope.expected_heads.items()
                ) or any(key in current_heads for key in scope.expected_absent)
            if changed:
                raise ResearchIdentityError("TEST_EXECUTION_PLAN_BASIS_CHANGED")
            commit = RevisionCommitService(
                self._ledger, self._clock, self._ids, policy_version=spec.policy_digest
            ).commit(
                RevisionChangeSet(
                    changeset_id=self._ids.new("changeset"),
                    project_id=context.project_id,
                    expected_heads={
                        **({} if scope is None else scope.expected_heads),
                        plan_key: current.revision_digest,
                    },
                    expected_absent_heads=() if scope is None else scope.expected_absent,
                    expected_head_set_digest=(
                        head_set_digest(dict(context.heads)) if scope is None else None
                    ),
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
        return _build_bound_execution_steps(context, action, spec, prediction_refs)

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
