from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import timedelta
from typing import cast

from pydantic import JsonValue

from thoth.application.services.action_plan_validation import validate_action_plan_dag
from thoth.application.services.authorization_consumption import (
    consume_authorization,
    decide_authorization,
)
from thoth.application.services.revision_service import CommitResult, RevisionCommitService
from thoth.domain.action_full import (
    ActionAuditRecord,
    ActionPlanRecord,
    ActionPortfolioRecord,
    ActionRecord,
    AuthorizationEnvelopeRecord,
)
from thoth.domain.actor import ActorRef
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.enums import ActorKind, CutoffState, EntityType
from thoth.domain.revision import (
    EntitySnapshot,
    ImpactPropagationPlan,
    RevisionChangeSet,
    SemanticRevision,
    StagedRevision,
)
from thoth.ports.action import ActionStorePort
from thoth.ports.artifact_ledger import ArtifactLedgerPort
from thoth.ports.decision_object import DecisionObjectStorePort
from thoth.ports.governance import GovernanceStorePort
from thoth.ports.hypothesis import HypothesisStorePort
from thoth.ports.ledger import LedgerPort, LedgerTransactionPort
from thoth.ports.runtime import AtomicUnitOfWorkPort, ClockPort, IdGeneratorPort

PURPOSES = {
    "INFORMATION_ACQUISITION",
    "HYPOTHESIS_DISCRIMINATION",
    "ANALYSIS_COMPUTATION",
    "SIMULATION",
    "EXPERIMENT_TEST",
    "STATE_OR_DESIGN_CHANGE",
    "RISK_REDUCTION",
    "REQUIREMENT_VERIFICATION",
    "COMMUNICATION_SUBMISSION",
    "GOVERNANCE_ESCALATION",
    "RESTORE_COMPENSATE",
}


def classify_effect_vector(
    effect: dict[str, object],
) -> tuple[str, str, tuple[str, ...], tuple[str, ...]]:
    complete = effect.get("effect_completeness_confirmed") is True
    if not complete:
        return (
            "R3",
            "POLICY_UNDEFINED",
            ("EFFECT_COMPLETENESS_REVIEW",),
            ("effect-owner",),
        )
    if any(
        effect.get(key) is True
        for key in (
            "changes_official_kpi",
            "grants_waiver",
            "changes_safety_threshold",
            "finalizes_model_weights",
        )
    ):
        return (
            "R4",
            "PROHIBITED",
            ("PROHIBITED_SEMANTIC_AUTHORITY",),
            ("institution-authority",),
        )
    if any(
        effect.get(key) is True
        for key in (
            "external_write",
            "physical_action",
            "changes_official_baseline",
            "operational_equipment_change",
        )
    ):
        roles = ["project-owner"]
        if effect.get("physical_action") is True:
            roles.append("safety-owner")
        if effect.get("external_write") is True:
            roles.append("external-interface-owner")
        return (
            "R3",
            "APPROVAL_REQUIRED",
            ("PROTECTED_ACTION", "ACTION_TIME_PREFLIGHT"),
            tuple(roles),
        )
    if effect.get("runs_untrusted_code") is True or effect.get("sandbox_required") is True:
        return (
            "R2",
            "AUTO_ALLOWED",
            ("ISOLATED_SANDBOX",),
            ("sandbox-owner",),
        )
    if effect.get("changes_local_draft") is True:
        return (
            "R1",
            "PREAUTHORIZED",
            ("LOCAL_REVISION",),
            (),
        )
    return "R0", "AUTO_ALLOWED", ("READ_ONLY",), ()


class ActionService:
    def __init__(
        self,
        *,
        store: ActionStorePort,
        objects: DecisionObjectStorePort,
        hypotheses: HypothesisStorePort,
        artifacts: ArtifactLedgerPort,
        governance: GovernanceStorePort,
        ledger: LedgerPort,
        commits: RevisionCommitService,
        semantic_uow: AtomicUnitOfWorkPort,
        clock: ClockPort,
        ids: IdGeneratorPort,
    ) -> None:
        self._store = store
        self._objects = objects
        self._hypotheses = hypotheses
        self._artifacts = artifacts
        self._governance = governance
        self._ledger = ledger
        self._commits = commits
        self._semantic_uow = semantic_uow
        self._clock = clock
        self._ids = ids

    def atomic(self) -> AbstractContextManager[LedgerTransactionPort]:
        return self._ledger.transaction()

    def now(self):
        return self._clock.now()

    def currentness(self, project_id: str, identifier: str, digest: str) -> dict[str, JsonValue]:
        from thoth.application.services.research_freshness import ResearchFreshnessService

        return (
            ResearchFreshnessService(self._ledger)
            .evaluate_entity(project_id, f"ACTION:{identifier}", digest)
            .model_dump(mode="json")
        )

    def create(
        self,
        *,
        project_id: str,
        object_id: str,
        portfolio_id: str,
        hypothesis_refs: tuple[str, ...],
        primary_purpose: str,
        secondary_purposes: tuple[str, ...],
        specification: dict[str, object],
        evidence_refs: tuple[str, ...],
    ) -> tuple[ActionRecord, tuple[str, ...], CommitResult]:
        self._object(project_id, object_id)
        self._validate_purposes(primary_purpose, secondary_purposes)
        self._validate_evidence(project_id, evidence_refs)
        self._validate_hypotheses(project_id, object_id, hypothesis_refs)
        effect = self._effect_vector(specification)
        risk_tier, policy_state, processes, roles = classify_effect_vector(effect)
        expected = specification.get("expected_observation_or_change")
        expected_mapping: dict[str, object] = (
            {str(key): child for key, child in cast(dict[object, object], expected).items()}
            if isinstance(expected, dict)
            else {"description": "UNRESOLVED", "status": "MISSING"}
        )
        missing = tuple(
            field
            for field, present in (
                ("expected_observation_or_change", bool(expected_mapping)),
                (
                    "effect_completeness_confirmed",
                    effect.get("effect_completeness_confirmed") is True,
                ),
                ("stop_conditions", bool(specification.get("stop_conditions"))),
                ("observability", bool(specification.get("observability"))),
            )
            if not present
        )
        duplicates = tuple(
            item.action_id
            for item in self._store.list_actions(project_id)
            if item.object_id == object_id
            and item.primary_purpose == primary_purpose
            and item.specification == specification
            and item.freshness != "INVALIDATED"
        )
        draft: dict[str, object] = {
            "action_revision_id": self._ids.new("action-revision"),
            "action_id": self._ids.new("action"),
            "project_id": project_id,
            "object_id": object_id,
            "portfolio_id": portfolio_id,
            "hypothesis_refs": hypothesis_refs,
            "primary_purpose": primary_purpose,
            "secondary_purposes": secondary_purposes,
            "specification": {**specification, "missing_fields": missing},
            "evidence_refs": evidence_refs,
            "expected_observation_or_change": expected_mapping,
            "effect_vector": effect,
            "impact_set": self._impact(effect),
            "risk_tier": risk_tier,
            "required_processes": processes,
            "required_roles": roles,
            "proposal_state": "CANDIDATE" if not missing else "DRAFT",
            "decision_state": "NOT_EVALUATED",
            "policy_state": policy_state,
            "authorization_state": (
                "NOT_REQUIRED" if risk_tier in {"R0", "R1", "R2", "R4"} else "NOT_PREPARED"
            ),
            "freshness": "CURRENT",
            "created_at": self._clock.now(),
        }
        record = ActionRecord.model_validate(
            {**draft, "revision_digest": self._digest("ACTION_RECORD", draft)}
        )
        committed, commit = self._persist_action(record, "action/created")
        return committed, duplicates, commit

    def revise(
        self,
        current: ActionRecord,
        *,
        updates: dict[str, object],
        event_type: str,
        evidence_refs: tuple[str, ...] = (),
        invalidate_downstream: bool = False,
    ) -> tuple[ActionRecord, CommitResult]:
        self._validate_evidence(current.project_id, evidence_refs)
        draft = current.model_dump(mode="python")
        draft.update(updates)
        if "specification" in updates:
            specification = updates["specification"]
            if not isinstance(specification, dict):
                raise ValueError("Action specification must be an object")
            specification_value = {
                str(key): child for key, child in cast(dict[object, object], specification).items()
            }
            effect = self._effect_vector(specification_value)
            risk_tier, policy_state, processes, roles = classify_effect_vector(effect)
            draft.update(
                {
                    "effect_vector": effect,
                    "impact_set": self._impact(effect),
                    "risk_tier": risk_tier,
                    "policy_state": policy_state,
                    "required_processes": processes,
                    "required_roles": roles,
                    "authorization_state": (
                        "NOT_REQUIRED" if risk_tier in {"R0", "R1", "R2", "R4"} else "NOT_PREPARED"
                    ),
                }
            )
        if invalidate_downstream:
            draft.update(
                {
                    "decision_state": "NOT_EVALUATED",
                    "proposal_state": "CANDIDATE",
                    "authorization_state": (
                        "STALE"
                        if current.authorization_state == "APPROVED"
                        else draft["authorization_state"]
                    ),
                    "freshness": "CURRENT",
                }
            )
        draft.update(
            {
                "action_revision_id": self._ids.new("action-revision"),
                "supersedes_revision_digest": current.revision_digest,
                "created_at": self._clock.now(),
            }
        )
        draft.pop("revision_digest", None)
        draft.pop("receipt_ref", None)
        revised = ActionRecord.model_validate(
            {**draft, "revision_digest": self._digest("ACTION_RECORD", draft)}
        )
        return self._persist_action(revised, event_type, evidence_refs=evidence_refs)

    def compose_portfolio(
        self,
        *,
        project_id: str,
        object_id: str,
        action_ids: tuple[str, ...],
        decision_need: str,
        criteria_proposal: tuple[dict[str, object], ...],
        portfolio_id: str | None = None,
    ) -> tuple[ActionPortfolioRecord, CommitResult]:
        self._object(project_id, object_id)
        actions = self._actions(project_id, action_ids)
        if any(item.object_id != object_id for item in actions):
            raise ValueError("ActionPortfolio cannot cross DecisionObject boundaries")
        if len(actions) < 2 or len({item.primary_purpose for item in actions}) < 2:
            raise ValueError("ActionPortfolio requires materially different alternatives")
        effective_id = portfolio_id or self._ids.new("action-portfolio")
        current = self._store.read_portfolio(project_id, effective_id, None)
        mandatory = tuple(item for item in criteria_proposal if item.get("mandatory") is True)
        enhancing = tuple(item for item in criteria_proposal if item.get("mandatory") is not True)
        if not mandatory:
            mandatory = (
                {
                    "criterion_id": "mandatory:policy",
                    "name": "policy admissibility",
                    "mandatory": True,
                    "rationale": "prohibited actions cannot enter the feasible set",
                },
            )
        draft: dict[str, object] = {
            "portfolio_revision_id": self._ids.new("action-portfolio-revision"),
            "portfolio_id": effective_id,
            "project_id": project_id,
            "object_id": object_id,
            "action_refs": action_ids,
            "decision_need": decision_need,
            "mandatory_criteria": mandatory,
            "enhancing_criteria": enhancing,
            "evaluation_method": "NOT_EVALUATED",
            "scenario_results": (),
            "uncertainty": "UNASSESSED",
            "sensitivity": "UNASSESSED",
            "value_of_information": "UNASSESSED",
            "decision_state": "NOT_EVALUATED",
            "supersedes_revision_digest": None if current is None else current.revision_digest,
            "created_at": self._clock.now(),
        }
        record = ActionPortfolioRecord.model_validate(
            {**draft, "revision_digest": self._digest("ACTION_PORTFOLIO", draft)}
        )
        return self._persist_portfolio(record, "action/portfolioUpdated")

    def evaluate_portfolio(
        self,
        current: ActionPortfolioRecord,
        *,
        evaluation_method: str,
        policy_criteria_ref: str,
        preference_inputs: tuple[dict[str, object], ...],
    ) -> tuple[ActionPortfolioRecord, CommitResult]:
        actions = self._actions(current.project_id, current.action_refs)
        results: list[dict[str, object]] = []
        feasible: list[str] = []
        for action in actions:
            mandatory_pass = action.policy_state != "PROHIBITED"
            if mandatory_pass:
                feasible.append(action.action_id)
            results.append(
                {
                    "action_id": action.action_id,
                    "mandatory_gate": "PASS" if mandatory_pass else "FAIL",
                    "risk_tier": action.risk_tier,
                    "impact": action.impact_set,
                    "expected": action.expected_observation_or_change,
                }
            )
        preference_required = len(feasible) > 1 and not preference_inputs
        state = (
            "PREFERENCE_REQUIRED"
            if preference_required
            else "RECOMMENDATION_READY"
            if feasible
            else "EVALUATED"
        )
        return self._revise_portfolio(
            current,
            updates={
                "evaluation_method": evaluation_method,
                "scenario_results": tuple(results),
                "uncertainty": "EXPLICIT_SCENARIO_COMPARISON",
                "sensitivity": (
                    "RANK_UNSTABLE_WITHOUT_PREFERENCES"
                    if preference_required
                    else "STABLE_WITH_SUPPLIED_PREFERENCES"
                ),
                "value_of_information": (
                    "PREFERENCE_INPUT_HAS_DECISION_VALUE"
                    if preference_required
                    else "NO_ADDITIONAL_PREFERENCE_REQUIRED"
                ),
                "decision_state": state,
                "recommendation_ref": None,
            },
            event_type="action/evaluated",
            audit_payload={"policy_criteria_ref": policy_criteria_ref},
        )

    def recommend(
        self, current: ActionPortfolioRecord, rationale: str
    ) -> tuple[ActionPortfolioRecord, CommitResult]:
        feasible = [
            item
            for item in self._actions(current.project_id, current.action_refs)
            if item.policy_state != "PROHIBITED"
        ]
        if not feasible:
            raise ValueError("no policy-admissible Action can be recommended")
        recommended = sorted(feasible, key=lambda item: (item.risk_tier, item.action_id))[0]
        return self._revise_portfolio(
            current,
            updates={
                "recommendation_ref": recommended.action_id,
                "decision_state": "RECOMMENDATION_READY",
                "sensitivity": current.sensitivity,
            },
            event_type="action/recommended",
            audit_payload={"rationale": rationale, "selection_implied": False},
        )

    def select(
        self,
        current: ActionPortfolioRecord,
        *,
        action_id: str,
        decision_context: dict[str, object],
        actor_ref: str,
    ) -> tuple[ActionRecord, ActionPortfolioRecord, CommitResult, CommitResult]:
        """Record a selection; execution authorization remains a separate phase."""
        # Either expected-head conflict must undo both the Action and portfolio publications.
        with self.atomic():
            action = self._store.read_action(current.project_id, action_id, None)
            if action is None or action.action_id not in current.action_refs:
                raise ValueError("selected Action is not in the portfolio")
            selected, action_commit = self.revise(
                action,
                updates={"proposal_state": "SELECTED", "decision_state": "EVALUATED"},
                event_type="action/selected",
            )
            portfolio, portfolio_commit = self._revise_portfolio(
                current,
                updates={"selected_action_ref": action_id},
                event_type="action/selected",
                audit_payload={
                    "decision_context": decision_context,
                    "actor_ref": actor_ref,
                    "authorization_implied": False,
                },
            )
            return selected, portfolio, action_commit, portfolio_commit

    def compose_plan(
        self,
        *,
        project_id: str,
        object_id: str,
        selected_action_refs: tuple[str, ...],
        step_candidates: tuple[dict[str, object], ...],
        dependency_edges: tuple[dict[str, str], ...],
        plan_id: str | None = None,
    ) -> tuple[ActionPlanRecord, CommitResult]:
        self._object(project_id, object_id)
        actions = self._actions(project_id, selected_action_refs)
        if any(item.object_id != object_id for item in actions):
            raise ValueError("ActionPlan cannot cross DecisionObject boundaries")
        steps = tuple(self._normalize_step(project_id, value) for value in step_candidates)
        if not steps:
            raise ValueError("ActionPlan requires at least one step")
        self._validate_dag(steps, dependency_edges)
        effective_id = plan_id or self._ids.new("action-plan")
        current = self._store.read_plan(project_id, effective_id, None)
        derived = self._derive_plan(steps, dependency_edges)
        draft: dict[str, object] = {
            "plan_revision_id": self._ids.new("action-plan-revision"),
            "plan_id": effective_id,
            "project_id": project_id,
            "object_id": object_id,
            "selected_action_refs": selected_action_refs,
            "steps": steps,
            "dependency_edges": dependency_edges,
            **derived,
            "authorization_refs": () if current is None else current.authorization_refs,
            "validation_state": "VALIDATED",
            "freshness": "CURRENT",
            "supersedes_revision_digest": None if current is None else current.revision_digest,
            "created_at": self._clock.now(),
        }
        record = ActionPlanRecord.model_validate(
            {**draft, "revision_digest": self._digest("ACTION_PLAN", draft)}
        )
        return self._persist_plan(record, "action/planComposed")

    def revise_plan(
        self,
        current: ActionPlanRecord,
        *,
        updates: dict[str, object],
        event_type: str,
        evidence_refs: tuple[str, ...] = (),
    ) -> tuple[ActionPlanRecord, CommitResult]:
        self._validate_evidence(current.project_id, evidence_refs)
        draft = current.model_dump(mode="python")
        draft.update(updates)
        steps_raw = draft.get("steps")
        edges_raw = draft.get("dependency_edges")
        if not isinstance(steps_raw, tuple) or not isinstance(edges_raw, tuple):
            raise ValueError("ActionPlan steps and edges must remain canonical tuples")
        steps = tuple(
            self._normalize_step(
                current.project_id,
                {str(key): child for key, child in cast(dict[object, object], item).items()},
            )
            for item in cast(tuple[object, ...], steps_raw)
            if isinstance(item, dict)
        )
        edges = tuple(
            {str(key): str(child) for key, child in cast(dict[object, object], item).items()}
            for item in cast(tuple[object, ...], edges_raw)
            if isinstance(item, dict)
        )
        self._validate_dag(steps, edges)
        draft["steps"] = steps
        draft["dependency_edges"] = edges
        draft.update(self._derive_plan(steps, edges))
        draft.update(
            {
                "plan_revision_id": self._ids.new("action-plan-revision"),
                "supersedes_revision_digest": current.revision_digest,
                "authorization_refs": (),
                "freshness": "CURRENT",
                "created_at": self._clock.now(),
            }
        )
        draft.pop("revision_digest", None)
        revised = ActionPlanRecord.model_validate(
            {**draft, "revision_digest": self._digest("ACTION_PLAN", draft)}
        )
        return self._persist_plan(revised, event_type, evidence_refs=evidence_refs)

    def prepare_authorization(
        self,
        plan: ActionPlanRecord,
        *,
        step_id: str,
        predecessor_output_digests: tuple[str, ...],
        target_baseline_digests: tuple[str, ...],
        policy_version: str,
    ) -> tuple[AuthorizationEnvelopeRecord | None, str]:
        step = self._step(plan, step_id)
        if step.get("risk_tier") == "R4":
            raise ValueError("R4 has no authorization transition or executor")
        if step.get("risk_tier") != "R3":
            return None, "NOT_REQUIRED"
        predecessors = {edge["from"] for edge in plan.dependency_edges if edge["to"] == step_id}
        if predecessors and len(predecessor_output_digests) < len(predecessors):
            return None, "PRECONDITION_NOT_READY"
        if not target_baseline_digests:
            return None, "PRECONDITION_NOT_READY"
        scope = {
            "project_id": plan.project_id,
            "plan_id": plan.plan_id,
            "step_id": step_id,
            "plan_revision_digest": plan.revision_digest,
            "predecessor_output_digests": predecessor_output_digests,
            "target_baseline_digests": target_baseline_digests,
            "policy_version": policy_version,
        }
        digest = domain_digest("ACTION_AUTHORIZATION_SCOPE", "1.0.0", canonical_payload(scope))
        draft: dict[str, object] = {
            "authorization_revision_id": self._ids.new("authorization-revision"),
            "authorization_id": self._ids.new("authorization"),
            "project_id": plan.project_id,
            "plan_id": plan.plan_id,
            "step_id": step_id,
            "plan_revision_digest": plan.revision_digest,
            "predecessor_output_digests": predecessor_output_digests,
            "target_baseline_digests": target_baseline_digests,
            "policy_version": policy_version,
            "exact_scope_digest": digest,
            "required_roles": self._string_tuple(step.get("required_roles", ())),
            "state": "PENDING",
            "decision_history": (),
            "expires_at": self._clock.now() + timedelta(hours=1),
            "single_use": True,
            "created_at": self._clock.now(),
        }
        envelope = AuthorizationEnvelopeRecord.model_validate(
            {**draft, "revision_digest": self._digest("AUTHORIZATION", draft)}
        )
        with self._semantic_uow.transaction():
            self._store.add_authorization(envelope)
            self.audit(
                envelope.project_id,
                envelope.authorization_id,
                "action/authorizationPrepared",
                {"exact_scope_digest": digest},
            )
        return envelope, "PENDING"

    def decide_authorization(
        self,
        current: AuthorizationEnvelopeRecord,
        *,
        decision: str,
        actor_ref: str,
        role_assignment_ref: str,
        approved_digest: str,
        reason: str | None,
        dissent: str | None,
    ) -> AuthorizationEnvelopeRecord:
        return decide_authorization(
            current,
            decision=decision,
            actor_ref=actor_ref,
            role_assignment_ref=role_assignment_ref,
            approved_digest=approved_digest,
            reason=reason,
            dissent=dissent,
            store=self._store,
            governance=self._governance,
            ledger=self._ledger,
            clock=self._clock,
            ids=self._ids,
            audit=self.audit,
        )

    def consume_authorization(
        self,
        current: AuthorizationEnvelopeRecord,
        *,
        exact_scope_digest: str,
    ) -> AuthorizationEnvelopeRecord:
        return consume_authorization(
            current,
            exact_scope_digest=exact_scope_digest,
            store=self._store,
            governance=self._governance,
            ledger=self._ledger,
            clock=self._clock,
            ids=self._ids,
            audit=self.audit,
        )

    def audit(
        self, project_id: str, subject_id: str, event_type: str, payload: dict[str, object]
    ) -> ActionAuditRecord:
        draft = {
            "project_id": project_id,
            "subject_id": subject_id,
            "event_type": event_type,
            "payload": payload,
            "created_at": self._clock.now(),
        }
        record = ActionAuditRecord.model_validate(
            {
                **draft,
                "audit_id": self._ids.new("action-audit"),
                "event_digest": domain_digest("ACTION_AUDIT", "1.0.0", canonical_payload(draft)),
            }
        )
        self._store.append_audit(record)
        return record

    def _persist_action(
        self,
        record: ActionRecord,
        event_type: str,
        *,
        evidence_refs: tuple[str, ...] = (),
    ) -> tuple[ActionRecord, CommitResult]:
        with self._semantic_uow.transaction():
            commit = self._commit(
                record,
                record.action_id,
                record.action_revision_id,
                record.revision_digest,
                record.supersedes_revision_digest,
                event_type,
                evidence_refs or record.evidence_refs,
            )
            self._store.add_action(record)
            self.audit(
                record.project_id,
                record.action_id,
                event_type,
                {"revision": record.revision_digest},
            )
        return record, commit

    def _persist_portfolio(
        self,
        record: ActionPortfolioRecord,
        event_type: str,
        *,
        extra_audit_payload: dict[str, object] | None = None,
    ) -> tuple[ActionPortfolioRecord, CommitResult]:
        with self._semantic_uow.transaction():
            commit = self._commit(
                record,
                record.portfolio_id,
                record.portfolio_revision_id,
                record.revision_digest,
                record.supersedes_revision_digest,
                event_type,
                (),
            )
            self._store.add_portfolio(record)
            self.audit(
                record.project_id,
                record.portfolio_id,
                event_type,
                {"revision": record.revision_digest},
            )
            if extra_audit_payload:
                self.audit(
                    record.project_id,
                    record.portfolio_id,
                    event_type,
                    extra_audit_payload,
                )
        return record, commit

    def _persist_plan(
        self,
        record: ActionPlanRecord,
        event_type: str,
        *,
        evidence_refs: tuple[str, ...] = (),
    ) -> tuple[ActionPlanRecord, CommitResult]:
        with self._semantic_uow.transaction():
            commit = self._commit(
                record,
                record.plan_id,
                record.plan_revision_id,
                record.revision_digest,
                record.supersedes_revision_digest,
                event_type,
                evidence_refs,
            )
            self._store.add_plan(record)
            self.audit(
                record.project_id,
                record.plan_id,
                event_type,
                {"revision": record.revision_digest},
            )
        return record, commit

    def _revise_portfolio(
        self,
        current: ActionPortfolioRecord,
        *,
        updates: dict[str, object],
        event_type: str,
        audit_payload: dict[str, object] | None = None,
    ) -> tuple[ActionPortfolioRecord, CommitResult]:
        draft = current.model_dump(mode="python")
        draft.update(updates)
        draft.update(
            {
                "portfolio_revision_id": self._ids.new("action-portfolio-revision"),
                "supersedes_revision_digest": current.revision_digest,
                "created_at": self._clock.now(),
            }
        )
        draft.pop("revision_digest", None)
        revised = ActionPortfolioRecord.model_validate(
            {**draft, "revision_digest": self._digest("ACTION_PORTFOLIO", draft)}
        )
        return self._persist_portfolio(
            revised,
            event_type,
            extra_audit_payload=audit_payload,
        )

    def _commit(
        self,
        record: ActionRecord | ActionPortfolioRecord | ActionPlanRecord,
        entity_id: str,
        revision_id: str,
        revision_digest: str,
        supersedes: str | None,
        event_type: str,
        evidence_refs: tuple[str, ...],
    ) -> CommitResult:
        content = record.model_dump(mode="python")
        snapshot = EntitySnapshot(
            snapshot_id=self._ids.new("snapshot"),
            project_id=record.project_id,
            entity_type=EntityType.ACTION,
            entity_id=entity_id,
            schema_version="1.0.0",
            content=content,
            content_digest=domain_digest("ENTITY_SNAPSHOT", "1.0.0", canonical_payload(content)),
        )
        actor = ActorRef(
            actor_id="agent:action-service",
            kind=ActorKind.AGENT,
            role="action-planner",
        )
        revision = SemanticRevision(
            revision_id=revision_id,
            project_id=record.project_id,
            entity_type=EntityType.ACTION,
            entity_id=entity_id,
            snapshot_id=snapshot.snapshot_id,
            parent_revision_digests=() if supersedes is None else (supersedes,),
            actor=actor,
            reason=event_type,
            evidence_refs=evidence_refs,
            affected_refs=(),
            revision_digest=revision_digest,
            created_at=record.created_at,
        )
        expected = {} if supersedes is None else {f"ACTION:{entity_id}": supersedes}
        commit = self._commits.commit(
            RevisionChangeSet(
                changeset_id=self._ids.new("changeset"),
                project_id=record.project_id,
                expected_heads=expected,
                staged_revisions=(StagedRevision(snapshot=snapshot, revision=revision),),
                impact_plan=ImpactPropagationPlan(),
                actor=actor,
                reason=event_type,
            )
        )
        if not commit.committed_revision_ids:
            raise ValueError("Action commit branched because expected revision changed")
        return commit

    def _object(self, project_id: str, object_id: str) -> None:
        if self._objects.read_object(project_id, object_id, None) is None:
            raise ValueError("DecisionObject was not found")

    def _validate_hypotheses(self, project_id: str, object_id: str, refs: tuple[str, ...]) -> None:
        for reference in refs:
            value = self._hypotheses.read_hypothesis(project_id, reference, None)
            if value is None or value.object_id != object_id:
                raise ValueError("Action references an unknown or cross-object hypothesis")

    def _validate_evidence(self, project_id: str, refs: tuple[str, ...]) -> None:
        for reference in refs:
            span = self._artifacts.read_evidence(reference)
            if span is None or span.project_id != project_id:
                raise ValueError("Action evidence ref is outside the project")
            if span.cutoff_state != CutoffState.ELIGIBLE:
                raise ValueError("ineligible evidence cannot enter Action planning")

    def _actions(self, project_id: str, refs: tuple[str, ...]) -> tuple[ActionRecord, ...]:
        values: list[ActionRecord] = []
        for reference in refs:
            value = self._store.read_action(project_id, reference, None)
            if value is None:
                raise ValueError(f"Action not found: {reference}")
            values.append(value)
        return tuple(values)

    @staticmethod
    def _validate_purposes(primary: str, secondary: tuple[str, ...]) -> None:
        if primary not in PURPOSES or any(item not in PURPOSES for item in secondary):
            raise ValueError("unsupported Action purpose")

    @staticmethod
    def _effect_vector(specification: dict[str, object]) -> dict[str, object]:
        raw = specification.get("effect_vector", {})
        effect: dict[str, object] = (
            {str(key): child for key, child in cast(dict[object, object], raw).items()}
            if isinstance(raw, dict)
            else {}
        )
        effect.setdefault(
            "effect_completeness_confirmed",
            specification.get("effect_completeness_confirmed", False),
        )
        return effect

    @staticmethod
    def _impact(effect: dict[str, object]) -> dict[str, object]:
        return {
            "data": effect.get("data", "NONE"),
            "code": effect.get("code", "NONE"),
            "configuration": effect.get("configuration", "NONE"),
            "equipment": effect.get("equipment", "NONE"),
            "external_institution": effect.get("external_write", False),
            "baseline": effect.get("changes_official_baseline", False),
            "security": effect.get("security_consequence", "NONE"),
            "privacy": effect.get("privacy_consequence", "NONE"),
            "safety": effect.get("safety_consequence", "NONE"),
            "legal": effect.get("legal_consequence", "NONE"),
            "cost": effect.get("cost", "UNRESOLVED"),
            "time": effect.get("time", "UNRESOLVED"),
            "observability": effect.get("observability", "UNRESOLVED"),
        }

    def _normalize_step(self, project_id: str, value: dict[str, object]) -> dict[str, object]:
        step = dict(value)
        step_id = step.get("step_id")
        if not isinstance(step_id, str) or not step_id:
            step["step_id"] = self._ids.new("action-step")
        effect_raw = step.get("effect_vector", {})
        effect: dict[str, object] = (
            {str(key): child for key, child in cast(dict[object, object], effect_raw).items()}
            if isinstance(effect_raw, dict)
            else {}
        )
        risk, policy, processes, roles = classify_effect_vector(effect)
        step.update(
            {
                "effect_vector": effect,
                "risk_tier": risk,
                "policy_state": policy,
                "required_processes": processes,
                "required_roles": roles,
                "impact": self._impact(effect),
                "state": step.get("state", "READY"),
            }
        )
        return step

    @staticmethod
    def _validate_dag(
        steps: tuple[dict[str, object], ...], edges: tuple[dict[str, str], ...]
    ) -> None:
        validate_action_plan_dag(steps, edges)

    def _derive_plan(
        self, steps: tuple[dict[str, object], ...], edges: tuple[dict[str, str], ...]
    ) -> dict[str, object]:
        predecessor_targets = {edge["to"] for edge in edges}
        frontier = tuple(
            str(step["step_id"])
            for step in steps
            if str(step["step_id"]) not in predecessor_targets
            and step.get("state") == "READY"
            and step.get("risk_tier") in {"R0", "R1", "R2"}
            and step.get("policy_state") in {"AUTO_ALLOWED", "PREAUTHORIZED"}
        )
        processes = tuple(
            dict.fromkeys(
                process
                for step in steps
                for process in self._string_tuple(step.get("required_processes", ()))
            )
        )
        roles = tuple(
            dict.fromkeys(
                role
                for step in steps
                for role in self._string_tuple(step.get("required_roles", ()))
            )
        )
        return {
            "cumulative_impact": {
                "per_step": {str(step["step_id"]): step.get("impact", {}) for step in steps},
                "max_risk_tier_display_only": max(
                    (str(step.get("risk_tier", "R0")) for step in steps),
                    default="R0",
                ),
            },
            "required_process_union": processes,
            "required_role_union": roles,
            "auto_executable_frontier": frontier,
            "point_of_no_return_steps": tuple(
                str(step["step_id"])
                for step in steps
                if self._mapping(step.get("effect_vector", {})).get("irreversible") is True
            ),
        }

    @staticmethod
    def _step(plan: ActionPlanRecord, step_id: str) -> dict[str, object]:
        for step in plan.steps:
            if step.get("step_id") == step_id:
                return step
        raise ValueError("ActionPlan step not found")

    @staticmethod
    def _string_tuple(value: object) -> tuple[str, ...]:
        if not isinstance(value, tuple | list):
            return ()
        return tuple(str(item) for item in cast(tuple[object, ...] | list[object], value))

    @staticmethod
    def _mapping(value: object) -> dict[str, object]:
        if not isinstance(value, dict):
            return {}
        return {str(key): child for key, child in cast(dict[object, object], value).items()}

    @staticmethod
    def _digest(kind: str, value: dict[str, object]) -> str:
        return domain_digest(kind, "1.0.0", canonical_payload(value))
