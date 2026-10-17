from __future__ import annotations

from thoth.application.services.baseline_service import BaselineService
from thoth.application.services.outcome_record_drafts import (
    BUILTIN_OUTCOME_PROFILES,
    assessed_series_updates,
    assessment_record,
    attribution_record,
    attribution_support,
    change_set_record,
    impact_record,
    judge_assessment,
    linked_phase_updates,
    missing_series_fields,
    outcome_profile_record,
    reassessed_record,
    series_record,
)
from thoth.application.services.outcome_revision_drafts import (
    commit_parts,
    revised_assessment_record,
    revised_series_record,
)
from thoth.application.services.revision_service import CommitResult, RevisionCommitService
from thoth.domain.action import ActionPlan
from thoth.domain.action_full import ActionPlanRecord
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.enums import CutoffState, EntityType
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
    ImpactPropagationPlan,
    RevisionChangeSet,
    StagedRevision,
)
from thoth.ports.action import ActionStorePort
from thoth.ports.artifact_ledger import ArtifactLedgerPort
from thoth.ports.decision_object import DecisionObjectStorePort
from thoth.ports.execution import ExecutionStorePort
from thoth.ports.ledger import LedgerPort
from thoth.ports.outcome import OutcomeStorePort
from thoth.ports.runtime import ClockPort, IdGeneratorPort


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
                self._store.put_profile(
                    outcome_profile_record(
                        profile_ref, name, purposes, required, dimensions, attribution
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
            missing = missing_series_fields(assessment_windows, comparison_baseline_set_digest)
            record = series_record(
                project_id,
                object_id,
                action_plan_revision_digest,
                planned_execution_ref,
                profile_ref,
                comparison_baseline_set_digest,
                assessment_windows,
                phases,
                ids=self._ids,
                clock=self._clock,
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
            return self._revise_series(
                current,
                updates=linked_phase_updates(current, phase, observation_refs, completeness),
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
            validity, limitations, attribution = judge_assessment(
                current, phase, observation_refs, comparator_refs
            )
            assessment = assessment_record(
                current,
                profile,
                phase,
                observation_refs,
                comparator_refs,
                assumptions,
                validity,
                limitations,
                attribution,
                ids=self._ids,
                clock=self._clock,
            )
            committed_assessment, assessment_commit = self._persist_assessment(
                assessment, "outcome/assessed"
            )
            series, series_commit = self._revise_series(
                current,
                updates=assessed_series_updates(current, phase, assessment, validity),
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
            record = reassessed_record(
                current, new_evidence_refs, reason, ids=self._ids, clock=self._clock
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
            supported, limitations = attribution_support(
                method, contextual_factor_refs, counterfactual_evidence_refs, evidence_refs
            )
            attribution = attribution_record(
                current,
                method,
                contextual_factor_refs,
                counterfactual_evidence_refs,
                evidence_refs,
                supported,
                limitations,
                ids=self._ids,
                clock=self._clock,
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
            record = change_set_record(
                current,
                proposed_entity_changes,
                impact_policy_ref,
                expected_project_head_set,
                ids=self._ids,
                clock=self._clock,
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
            record = impact_record(
                series,
                broader_window,
                impact_profile_ref,
                evidence_refs,
                attribution_design_ref,
                ids=self._ids,
                clock=self._clock,
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
        record = revised_series_record(current, updates, ids=self._ids, clock=self._clock)
        return self._persist_series(record, event_type)

    def _revise_assessment(
        self,
        current: OutcomeAssessmentRecord,
        *,
        updates: dict[str, object],
        event_type: str,
    ) -> tuple[OutcomeAssessmentRecord, CommitResult]:
        record = revised_assessment_record(current, updates, ids=self._ids, clock=self._clock)
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
        snapshot, actor, revision = commit_parts(
            record,
            entity_id,
            revision_id,
            revision_digest,
            supersedes,
            event_type,
            evidence_refs,
            ids=self._ids,
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
