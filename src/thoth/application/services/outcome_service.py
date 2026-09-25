from __future__ import annotations

from thoth.application.services.baseline_service import BaselineService
from thoth.application.services.revision_service import CommitResult, RevisionCommitService
from thoth.domain.action import ActionPlan
from thoth.domain.action_full import ActionPlanRecord
from thoth.domain.actor import ActorRef
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.enums import ActorKind, CutoffState, EntityType
from thoth.domain.outcome_full import (
    AttributionAssessmentRecord,
    OutcomeAssessmentRecord,
    OutcomeAuditRecord,
    OutcomeChangeSetRecord,
    OutcomeImpactRecord,
    OutcomeProfileRecord,
    OutcomeSeriesRecord,
)
from thoth.domain.research_identity import decode_research
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
from thoth.ports.execution import ExecutionStorePort
from thoth.ports.ledger import LedgerPort
from thoth.ports.outcome import OutcomeStorePort
from thoth.ports.runtime import ClockPort, IdGeneratorPort

BUILTIN_OUTCOME_PROFILES = (
    (
        "REQUIREMENT_VERIFICATION_OUTCOME",
        "Requirement verification outcome",
        ("REQUIREMENT_VERIFICATION", "EXPERIMENT_TEST"),
        ("observation", "comparator", "criterion"),
        ("requirement_result", "effect_direction", "residual_risk"),
        "CONTRIBUTION_ONLY_WITHOUT_CAUSAL_DESIGN",
    ),
    (
        "EXPERIMENT_LEARNING_OUTCOME",
        "Experiment learning outcome",
        ("HYPOTHESIS_DISCRIMINATION", "ANALYSIS_COMPUTATION", "SIMULATION"),
        ("observation", "comparator", "test_validity"),
        ("hypothesis_discrimination", "information_gain", "unexpected_effect"),
        "ASSOCIATION_BY_DEFAULT",
    ),
    (
        "DELIVERY_ACKNOWLEDGEMENT_OUTCOME",
        "Delivery acknowledgement outcome",
        ("COMMUNICATION_SUBMISSION",),
        ("delivery_receipt",),
        ("delivery_acknowledgement",),
        "NOT_APPLICABLE",
    ),
)


class OutcomeService:
    def __init__(
        self,
        *,
        store: OutcomeStorePort,
        objects: DecisionObjectStorePort,
        actions: ActionStorePort,
        executions: ExecutionStorePort,
        artifacts: ArtifactLedgerPort,
        ledger: LedgerPort,
        commits: RevisionCommitService,
        clock: ClockPort,
        ids: IdGeneratorPort,
        baselines: BaselineService | None = None,
    ) -> None:
        self._store = store
        self._objects = objects
        self._actions = actions
        self._executions = executions
        self._artifacts = artifacts
        self._ledger = ledger
        self._commits = commits
        self._clock = clock
        self._ids = ids
        self._baselines = baselines

    def seed_profiles(self) -> None:
        with self._ledger.transaction():
            for (
                profile_ref,
                name,
                purposes,
                required,
                dimensions,
                attribution,
            ) in BUILTIN_OUTCOME_PROFILES:
                draft: dict[str, object] = {
                    "profile_ref": profile_ref,
                    "version": 1,
                    "name": name,
                    "action_purposes": purposes,
                    "required_evidence": required,
                    "comparator_contract": "EXACT_BASELINE_SET_DIGEST_REQUIRED",
                    "window_contract": "PHASE_AND_TIME_WINDOW_REQUIRED",
                    "evaluator_contract": "SOURCE_BOUND_DETERMINISTIC_OR_EXPLICIT_ABSTENTION",
                    "effect_dimensions": dimensions,
                    "attribution_standard": attribution,
                    "closure_criteria": {
                        "validity": "VALID_OR_PROFILE_ACCEPTED_LIMITED",
                        "objective": "EXPLICITLY_ASSESSED",
                        "residual_effect": "HANDLED_OR_ACCEPTED",
                    },
                    "enabled": True,
                    "authority_owner": "OUTCOME_EVALUATION_OWNER",
                }
                self._store.put_profile(
                    OutcomeProfileRecord.model_validate(
                        {
                            **draft,
                            "profile_digest": self._digest("OUTCOME_PROFILE", draft),
                        }
                    )
                )

    def create_series(
        self,
        *,
        project_id: str,
        object_id: str,
        action_plan_revision_digest: str,
        planned_execution_ref: str | None,
        profile_ref: str,
        comparison_baseline_set_digest: str,
        assessment_windows: tuple[dict[str, object], ...],
    ) -> tuple[OutcomeSeriesRecord, tuple[str, ...], CommitResult]:
        with self._ledger.transaction():
            if self._objects.read_object(project_id, object_id, None) is None:
                raise ValueError("DecisionObject was not found")
            plan_known = any(
                plan.revision_digest == action_plan_revision_digest
                for plan in self._actions.list_plans(project_id)
            )
            if not plan_known and not self._is_plan_revision(
                project_id, action_plan_revision_digest
            ):
                raise ValueError("ActionPlan revision was not found")
            if (
                planned_execution_ref is not None
                and self._executions.read_execution(project_id, planned_execution_ref) is None
            ):
                raise ValueError("planned Execution was not found")
            self._profile(profile_ref, None)
            if self._baselines is not None and self._baselines.list_sets(project_id):
                self._baselines.require_current_comparator(
                    project_id,
                    comparison_baseline_set_digest,
                )
            phases = tuple(str(window.get("assessment_phase", "")) for window in assessment_windows)
            allowed = {"INTERIM", "FOLLOW_UP", "FINAL_WITHIN_SCOPE"}
            if not phases or any(phase not in allowed for phase in phases):
                raise ValueError("Outcome windows require valid, explicit assessment phases")
            missing = tuple(
                field
                for field, present in (
                    ("assessment_windows", bool(assessment_windows)),
                    ("comparison_baseline_set_digest", bool(comparison_baseline_set_digest)),
                    ("profile", True),
                )
                if not present
            )
            draft: dict[str, object] = {
                "series_revision_id": self._ids.new("outcome-series-revision"),
                "outcome_series_id": self._ids.new("outcome-series"),
                "project_id": project_id,
                "object_id": object_id,
                "action_plan_revision_digest": action_plan_revision_digest,
                "planned_execution_ref": planned_execution_ref,
                "profile_ref": profile_ref,
                "comparison_baseline_set_digest": comparison_baseline_set_digest,
                "assessment_windows": assessment_windows,
                "phase_states": {phase: "WAITING_OBSERVATION" for phase in phases},
                "observation_refs_by_phase": {},
                "observation_completeness_by_phase": {},
                "assessment_refs": (),
                "final_within_scope_state": "NOT_ASSESSED",
                "revision": 0,
                "created_at": self._clock.now(),
            }
            record = OutcomeSeriesRecord.model_validate(
                {**draft, "revision_digest": self._digest("OUTCOME_SERIES", draft)}
            )
            committed, commit = self._persist_series(record, "outcome/seriesCreated")
            return committed, missing, commit

    def link_observations(
        self,
        current: OutcomeSeriesRecord,
        *,
        phase: str,
        observation_refs: tuple[str, ...],
        completeness: str,
        evidence_refs: tuple[str, ...],
    ) -> tuple[OutcomeSeriesRecord, CommitResult]:
        with self._ledger.transaction():
            self._validate_evidence(current.project_id, (*observation_refs, *evidence_refs))
            if phase not in current.phase_states:
                raise ValueError("assessment phase is not planned in this OutcomeSeries")
            refs = dict(current.observation_refs_by_phase)
            refs[phase] = observation_refs
            completeness_by_phase = dict(current.observation_completeness_by_phase)
            completeness_by_phase[phase] = completeness
            states = dict(current.phase_states)
            states[phase] = (
                "READY_TO_ASSESS"
                if observation_refs and completeness in {"COMPLETE", "PARTIAL"}
                else "WAITING_OBSERVATION"
            )
            return self._revise_series(
                current,
                updates={
                    "observation_refs_by_phase": refs,
                    "observation_completeness_by_phase": completeness_by_phase,
                    "phase_states": states,
                },
                event_type="outcome/observationLinked",
            )

    def assess(
        self,
        current: OutcomeSeriesRecord,
        *,
        phase: str,
        profile_version: int,
        observation_refs: tuple[str, ...],
        comparator_refs: tuple[str, ...],
        assumptions: tuple[str, ...],
    ) -> tuple[OutcomeAssessmentRecord, OutcomeSeriesRecord, CommitResult, CommitResult]:
        with self._ledger.transaction():
            profile = self._profile(current.profile_ref, profile_version)
            if phase not in current.phase_states:
                raise ValueError("assessment phase is not planned")
            self._validate_evidence(current.project_id, (*observation_refs, *comparator_refs))
            completeness = current.observation_completeness_by_phase.get(phase, "MISSING")
            validity = (
                "INVALID"
                if completeness == "CORRUPT"
                else "VALID"
                if completeness == "COMPLETE" and observation_refs and comparator_refs
                else "LIMITED"
                if completeness == "PARTIAL" and observation_refs
                else "INCONCLUSIVE"
            )
            limitations: list[str] = []
            if not comparator_refs:
                limitations.append("comparator evidence is missing")
            if completeness != "COMPLETE":
                limitations.append(f"observation completeness is {completeness}")
            attribution = (
                "ASSOCIATED"
                if current.planned_execution_ref and observation_refs
                else "NOT_ASSESSED"
            )
            draft: dict[str, object] = {
                "assessment_revision_id": self._ids.new("outcome-assessment-revision"),
                "outcome_assessment_id": self._ids.new("outcome-assessment"),
                "project_id": current.project_id,
                "outcome_series_id": current.outcome_series_id,
                "object_id": current.object_id,
                "assessment_phase": phase,
                "plan_revision_digest": current.action_plan_revision_digest,
                "execution_ref": current.planned_execution_ref,
                "baseline_set_digest": current.comparison_baseline_set_digest,
                "profile_ref": profile.profile_ref,
                "profile_version": profile.version,
                "expected_observation_refs": (),
                "actual_observation_refs": observation_refs,
                "comparator_refs": comparator_refs,
                "assumptions": assumptions,
                "lifecycle": "ASSESSED",
                "validity": validity,
                "objective_attainment": "NOT_ASSESSED",
                "attribution_state": attribution,
                "follow_up_state": "MORE_EVIDENCE_REQUIRED",
                "limitations": tuple(limitations),
                "changed_dimensions": tuple(profile.effect_dimensions),
                "created_at": self._clock.now(),
            }
            assessment = OutcomeAssessmentRecord.model_validate(
                {**draft, "revision_digest": self._digest("OUTCOME_ASSESSMENT", draft)}
            )
            committed_assessment, assessment_commit = self._persist_assessment(
                assessment, "outcome/assessed"
            )
            states = dict(current.phase_states)
            states[phase] = "ASSESSED"
            series, series_commit = self._revise_series(
                current,
                updates={
                    "phase_states": states,
                    "assessment_refs": (
                        *current.assessment_refs,
                        assessment.outcome_assessment_id,
                    ),
                    "final_within_scope_state": (
                        validity
                        if phase == "FINAL_WITHIN_SCOPE"
                        else current.final_within_scope_state
                    ),
                },
                event_type="outcome/assessed",
            )
            return committed_assessment, series, assessment_commit, series_commit

    def reassess(
        self,
        current: OutcomeAssessmentRecord,
        *,
        new_evidence_refs: tuple[str, ...],
        reason: str,
    ) -> tuple[OutcomeAssessmentRecord, CommitResult]:
        with self._ledger.transaction():
            self._validate_evidence(current.project_id, new_evidence_refs)
            draft = current.model_dump(mode="python")
            merged = tuple(dict.fromkeys((*current.actual_observation_refs, *new_evidence_refs)))
            changed = ("evidence_coverage",) if merged != current.actual_observation_refs else ()
            draft.update(
                {
                    "assessment_revision_id": self._ids.new("outcome-assessment-revision"),
                    "actual_observation_refs": merged,
                    "lifecycle": "ASSESSED",
                    "validity": (
                        "LIMITED"
                        if current.validity == "INCONCLUSIVE" and merged
                        else current.validity
                    ),
                    "changed_dimensions": changed,
                    "limitations": (*current.limitations, f"reassessment reason: {reason}"),
                    "supersedes_revision_digest": current.revision_digest,
                    "created_at": self._clock.now(),
                }
            )
            draft.pop("revision_digest", None)
            record = OutcomeAssessmentRecord.model_validate(
                {**draft, "revision_digest": self._digest("OUTCOME_ASSESSMENT", draft)}
            )
            return self._persist_assessment(record, "outcome/reassessed")

    def assess_attribution(
        self,
        current: OutcomeAssessmentRecord,
        *,
        method: str,
        contextual_factor_refs: tuple[str, ...],
        counterfactual_evidence_refs: tuple[str, ...],
        evidence_refs: tuple[str, ...],
    ) -> tuple[AttributionAssessmentRecord, OutcomeAssessmentRecord, CommitResult]:
        with self._ledger.transaction():
            self._validate_evidence(
                current.project_id, (*counterfactual_evidence_refs, *evidence_refs)
            )
            causal_methods = {"RANDOMIZED", "QUASI_EXPERIMENTAL", "VALID_N_OF_1"}
            contribution_methods = {"CONTRIBUTION_ANALYSIS", "PROCESS_TRACING"}
            supported = (
                "CAUSAL_ATTRIBUTION_SUPPORTED"
                if method in causal_methods and counterfactual_evidence_refs and evidence_refs
                else "CONTRIBUTION_SUPPORTED"
                if method in contribution_methods and evidence_refs
                else "ASSOCIATED"
                if evidence_refs
                else "INCONCLUSIVE"
            )
            limitations: list[str] = []
            if supported != "CAUSAL_ATTRIBUTION_SUPPORTED":
                limitations.append("causal attribution design/evidence is insufficient")
            if not contextual_factor_refs:
                limitations.append("contextual factors are not explicitly represented")
            draft: dict[str, object] = {
                "attribution_assessment_id": self._ids.new("outcome-attribution"),
                "project_id": current.project_id,
                "outcome_assessment_id": current.outcome_assessment_id,
                "method": method,
                "contextual_factor_refs": contextual_factor_refs,
                "counterfactual_evidence_refs": counterfactual_evidence_refs,
                "evidence_refs": evidence_refs,
                "supported_level": supported,
                "limitations": tuple(limitations),
                "created_at": self._clock.now(),
            }
            attribution = AttributionAssessmentRecord.model_validate(
                {**draft, "attribution_digest": self._digest("OUTCOME_ATTRIBUTION", draft)}
            )
            self._store.add_attribution(attribution)
            revised, commit = self._revise_assessment(
                current,
                updates={
                    "attribution_state": supported,
                    "attribution_ref": attribution.attribution_assessment_id,
                },
                event_type="outcome/attributionUpdated",
            )
            return attribution, revised, commit

    def propose_change_set(
        self,
        current: OutcomeAssessmentRecord,
        *,
        proposed_entity_changes: dict[str, object],
        impact_policy_ref: str,
        expected_project_head_set: str,
    ) -> OutcomeChangeSetRecord:
        with self._ledger.transaction():
            restrictions: list[str] = []
            if current.validity in {"INVALID", "LIMITED", "INCONCLUSIVE"}:
                restrictions.extend(
                    (
                        "NO_OFFICIAL_CRITERION_CHANGE",
                        "NO_HYPOTHESIS_APPRAISAL_MUTATION",
                        "NO_BASELINE_MOVEMENT",
                        "NO_INSTITUTIONAL_DISPOSITION",
                    )
                )
            candidate_revisions = tuple(
                {
                    "entity_namespace": namespace,
                    "candidate_patch": patch,
                    "applied": False,
                }
                for namespace, patch in proposed_entity_changes.items()
            )
            draft: dict[str, object] = {
                "outcome_change_set_id": self._ids.new("outcome-change-set"),
                "project_id": current.project_id,
                "outcome_assessment_id": current.outcome_assessment_id,
                "proposed_entity_changes": proposed_entity_changes,
                "impact_policy_ref": impact_policy_ref,
                "expected_project_head_set": expected_project_head_set,
                "candidate_revisions": candidate_revisions,
                "impact_propagation_plan": {
                    "stale_refs": tuple(proposed_entity_changes),
                    "invalidated_refs": (),
                    "recalculate_refs": tuple(proposed_entity_changes),
                },
                "validity_restrictions": tuple(restrictions),
                "status": "PROPOSED_NOT_APPLIED",
                "created_at": self._clock.now(),
            }
            record = OutcomeChangeSetRecord.model_validate(
                {**draft, "change_set_digest": self._digest("OUTCOME_CHANGE_SET", draft)}
            )
            self._store.add_change_set(record)
            self.audit(
                current.project_id,
                current.outcome_assessment_id,
                "outcome/changeSetProposed",
                {"change_set_id": record.outcome_change_set_id},
            )
            return record

    def propose_impact(
        self,
        series: OutcomeSeriesRecord,
        *,
        broader_window: str,
        impact_profile_ref: str,
        evidence_refs: tuple[str, ...],
        attribution_design_ref: str,
    ) -> OutcomeImpactRecord:
        with self._ledger.transaction():
            self._validate_evidence(series.project_id, evidence_refs)
            sufficient = bool(
                evidence_refs
                and broader_window
                and attribution_design_ref
                and attribution_design_ref != "TEMPORAL_ONLY"
            )
            draft: dict[str, object] = {
                "impact_assessment_id": self._ids.new("outcome-impact"),
                "project_id": series.project_id,
                "outcome_series_id": series.outcome_series_id,
                "broader_window": broader_window,
                "impact_profile_ref": impact_profile_ref,
                "evidence_refs": evidence_refs,
                "attribution_design_ref": attribution_design_ref,
                "status": ("CANDIDATE" if sufficient else "INSUFFICIENT_CAUSAL_DESIGN"),
                "contextual_factors": (),
                "limitations": (
                    ()
                    if sufficient
                    else ("broader impact cannot be inferred from one execution alone",)
                ),
                "created_at": self._clock.now(),
            }
            record = OutcomeImpactRecord.model_validate(
                {**draft, "impact_digest": self._digest("OUTCOME_IMPACT", draft)}
            )
            self._store.add_impact(record)
            return record

    def audit(
        self, project_id: str, subject_id: str, event_type: str, payload: dict[str, object]
    ) -> OutcomeAuditRecord:
        created_at = self._clock.now()
        draft: dict[str, object] = {
            "project_id": project_id,
            "subject_id": subject_id,
            "event_type": event_type,
            "payload": payload,
            "created_at": created_at,
        }
        record = OutcomeAuditRecord(
            audit_id=self._ids.new("outcome-audit"),
            project_id=project_id,
            subject_id=subject_id,
            event_type=event_type,
            payload=payload,
            event_digest=self._digest("OUTCOME_AUDIT", draft),
            created_at=created_at,
        )
        self._store.append_audit(record)
        return record

    def _revise_series(
        self,
        current: OutcomeSeriesRecord,
        *,
        updates: dict[str, object],
        event_type: str,
    ) -> tuple[OutcomeSeriesRecord, CommitResult]:
        draft = current.model_dump(mode="python")
        draft.update(updates)
        draft.update(
            {
                "series_revision_id": self._ids.new("outcome-series-revision"),
                "revision": current.revision + 1,
                "supersedes_revision_digest": current.revision_digest,
                "created_at": self._clock.now(),
            }
        )
        draft.pop("revision_digest", None)
        record = OutcomeSeriesRecord.model_validate(
            {**draft, "revision_digest": self._digest("OUTCOME_SERIES", draft)}
        )
        return self._persist_series(record, event_type)

    def _revise_assessment(
        self,
        current: OutcomeAssessmentRecord,
        *,
        updates: dict[str, object],
        event_type: str,
    ) -> tuple[OutcomeAssessmentRecord, CommitResult]:
        draft = current.model_dump(mode="python")
        draft.update(updates)
        draft.update(
            {
                "assessment_revision_id": self._ids.new("outcome-assessment-revision"),
                "supersedes_revision_digest": current.revision_digest,
                "created_at": self._clock.now(),
            }
        )
        draft.pop("revision_digest", None)
        record = OutcomeAssessmentRecord.model_validate(
            {**draft, "revision_digest": self._digest("OUTCOME_ASSESSMENT", draft)}
        )
        return self._persist_assessment(record, event_type)

    def _persist_series(
        self, record: OutcomeSeriesRecord, event_type: str
    ) -> tuple[OutcomeSeriesRecord, CommitResult]:
        with self._ledger.transaction():
            commit = self._commit(
                record,
                record.outcome_series_id,
                record.series_revision_id,
                record.revision_digest,
                record.supersedes_revision_digest,
                event_type,
                (),
            )
            self._store.add_series(record)
            self.audit(record.project_id, record.outcome_series_id, event_type, {})
            return record, commit

    def _persist_assessment(
        self, record: OutcomeAssessmentRecord, event_type: str
    ) -> tuple[OutcomeAssessmentRecord, CommitResult]:
        with self._ledger.transaction():
            commit = self._commit(
                record,
                record.outcome_assessment_id,
                record.assessment_revision_id,
                record.revision_digest,
                record.supersedes_revision_digest,
                event_type,
                record.actual_observation_refs,
            )
            self._store.add_assessment(record)
            self.audit(record.project_id, record.outcome_assessment_id, event_type, {})
            return record, commit

    def _commit(
        self,
        record: OutcomeSeriesRecord | OutcomeAssessmentRecord,
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
            entity_type=EntityType.OUTCOME,
            entity_id=entity_id,
            schema_version="1.0.0",
            content=content,
            content_digest=self._digest("ENTITY_SNAPSHOT", content),
        )
        actor = ActorRef(
            actor_id="agent:outcome-evaluator",
            kind=ActorKind.AGENT,
            role="outcome-evaluator",
        )
        revision = SemanticRevision(
            revision_id=revision_id,
            project_id=record.project_id,
            entity_type=EntityType.OUTCOME,
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
        expected = {} if supersedes is None else {f"OUTCOME:{entity_id}": supersedes}
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
            raise ValueError("Outcome commit branched because expected revision changed")
        return commit

    def _is_plan_revision(self, project: str, digest: str) -> bool:
        revision = self._ledger.read_revision_by_digest(project, digest)
        snapshot = None if revision is None else self._ledger.read_snapshot(revision.snapshot_id)
        if revision is None or snapshot is None or revision.entity_type != EntityType.ACTION:
            return False
        return isinstance(
            decode_research(revision, snapshot).record, (ActionPlan, ActionPlanRecord)
        )

    def _profile(self, profile_ref: str, version: int | None) -> OutcomeProfileRecord:
        profile = self._store.read_profile(profile_ref, version)
        if profile is None or not profile.enabled:
            raise ValueError("OutcomeProfile is unavailable")
        return profile

    def _validate_evidence(self, project_id: str, refs: tuple[str, ...]) -> None:
        for reference in refs:
            span = self._artifacts.read_evidence(reference)
            if span is None or span.project_id != project_id:
                raise ValueError("Outcome evidence ref is outside the project")
            if span.cutoff_state != CutoffState.ELIGIBLE:
                raise ValueError("ineligible evidence cannot enter Outcome")

    @staticmethod
    def _digest(kind: str, value: dict[str, object]) -> str:
        return domain_digest(kind, "1.0.0", canonical_payload(value))
