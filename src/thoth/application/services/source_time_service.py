from __future__ import annotations

from datetime import datetime

from thoth.application.services.evidence_graph_service import EvidenceGraphService
from thoth.application.services.scoped_artifacts import ScopedArtifactLedger
from thoth.domain.artifact import StructuralDocument
from thoth.domain.auth import current_authenticated_actor
from thoth.domain.enums import CutoffState
from thoth.domain.source_time import (
    SourceTimeAssertion,
    SourceTimeAssessment,
    SourceTimeAssessmentMode,
    SourceTimeError,
    SourceTimeMutationBasis,
    SourceTimeMutationReason,
    SourceTimeReasonCode,
    build_source_time_assessment,
    classify_document_time,
    utc_microseconds,
)
from thoth.ports.ledger import LedgerPort
from thoth.ports.project import ProjectStorePort
from thoth.ports.runtime import ClockPort, IdGeneratorPort


class SourceTimeService:
    def __init__(
        self,
        *,
        artifacts: ScopedArtifactLedger,
        projects: ProjectStorePort,
        evidence_graph: EvidenceGraphService,
        ledger: LedgerPort,
        clock: ClockPort,
        ids: IdGeneratorPort,
    ) -> None:
        self._artifacts = artifacts
        self._projects = projects
        self._evidence_graph = evidence_graph
        self._ledger = ledger
        self._clock = clock
        self._ids = ids

    def assess_ingestion(
        self,
        document: StructuralDocument,
        *,
        source_version_id: str,
        cutoff_at: datetime,
        project_revision: int,
        classify: bool,
        current_cutoff_state: CutoffState | None = None,
    ) -> StructuralDocument:
        if not classify:
            return document
        classification = classify_document_time(
            document.document_time_observations,
            cutoff_at,
            current_cutoff_state=current_cutoff_state or document.artifact.cutoff_state,
        )
        if (
            current_cutoff_state or document.artifact.cutoff_state
        ) == CutoffState.PROHIBITED_CONTEXT:
            cutoff_state = CutoffState.PROHIBITED_CONTEXT
            reason = SourceTimeReasonCode.NO_DOCUMENT_DATE
        else:
            cutoff_state = classification.cutoff_state
            reason = classification.reason_code
        assessment = build_source_time_assessment(
            project_id=document.artifact.project_id,
            artifact_id=document.artifact.artifact_id,
            source_version_id=source_version_id,
            byte_sha256=document.artifact.byte_sha256,
            cutoff_at=utc_microseconds(cutoff_at),
            cutoff_state=cutoff_state,
            mode=SourceTimeAssessmentMode.AUTO,
            basis_observation_ids=classification.basis_observation_ids,
            reason_code=reason,
            revision=0,
            assessed_at=self._clock.now(),
        )
        artifact = document.artifact.model_copy(update={"cutoff_state": cutoff_state})
        return document.model_copy(
            update={"artifact": artifact, "source_time_assessment": assessment}
        )

    def confirm_unknown(
        self,
        basis: SourceTimeMutationBasis,
        assertion: SourceTimeAssertion,
    ) -> SourceTimeAssessment:
        actor = current_authenticated_actor()
        actor_id = None if actor is None else actor.actor_id
        return self._mutate(
            basis,
            next_state=(
                CutoffState.ELIGIBLE
                if assertion == SourceTimeAssertion.ON_OR_BEFORE_CUTOFF
                else CutoffState.AFTER_CUTOFF
            ),
            mode=SourceTimeAssessmentMode.USER_CONFIRMATION,
            reason=(
                SourceTimeReasonCode.USER_CONFIRMED_ON_OR_BEFORE
                if assertion == SourceTimeAssertion.ON_OR_BEFORE_CUTOFF
                else SourceTimeReasonCode.USER_CONFIRMED_AFTER
            ),
            require_unknown=True,
            actor_id=actor_id,
        )

    def correct_time(
        self,
        basis: SourceTimeMutationBasis,
        *,
        assertion: SourceTimeAssertion | None,
        revert_unknown: bool,
        correction_reason: str,
    ) -> SourceTimeAssessment:
        actor = current_authenticated_actor()
        actor_id = None if actor is None else actor.actor_id
        if revert_unknown:
            return self._mutate(
                basis,
                next_state=CutoffState.UNKNOWN_TIME,
                mode=SourceTimeAssessmentMode.ADVANCED_CORRECTION,
                reason=SourceTimeReasonCode.REVERTED_TO_UNKNOWN,
                require_unknown=False,
                actor_id=actor_id,
                correction_reason=correction_reason,
                allow_resolved=True,
            )
        if assertion is None:
            raise SourceTimeError(
                SourceTimeMutationReason.SOURCE_TIME_CORRECTION_REQUIRED.value,
                "advanced correction requires a relative cutoff assertion",
            )
        return self._mutate(
            basis,
            next_state=(
                CutoffState.ELIGIBLE
                if assertion == SourceTimeAssertion.ON_OR_BEFORE_CUTOFF
                else CutoffState.AFTER_CUTOFF
            ),
            mode=SourceTimeAssessmentMode.ADVANCED_CORRECTION,
            reason=SourceTimeReasonCode.ADVANCED_DATE_CORRECTION,
            require_unknown=False,
            actor_id=actor_id,
            correction_reason=correction_reason,
            allow_resolved=True,
        )

    def _mutate(
        self,
        basis: SourceTimeMutationBasis,
        *,
        next_state: CutoffState,
        mode: SourceTimeAssessmentMode,
        reason: SourceTimeReasonCode,
        require_unknown: bool,
        actor_id: str | None,
        correction_reason: str | None = None,
        allow_resolved: bool = False,
    ) -> SourceTimeAssessment:
        with self._ledger.transaction():
            project = self._projects.read(basis.project_id)
            if project is None:
                raise SourceTimeError(
                    SourceTimeMutationReason.SOURCE_TIME_BASIS_STALE.value,
                    "project was not found for source time mutation",
                )
            if project.revision != basis.expected_project_revision:
                raise SourceTimeError(
                    SourceTimeMutationReason.SOURCE_TIME_BASIS_STALE.value,
                    "project revision changed before source time mutation",
                )
            if utc_microseconds(project.cutoff_at) != utc_microseconds(basis.expected_cutoff_at):
                raise SourceTimeError(
                    SourceTimeMutationReason.SOURCE_TIME_BASIS_STALE.value,
                    "project cutoff changed before source time mutation",
                )
            current = self._artifacts.read_source_time(
                basis.project_id, basis.artifact_id, basis.source_version_id
            )
            if (
                current is not None
                and current.cutoff_state == CutoffState.AFTER_CUTOFF
                and next_state == CutoffState.ELIGIBLE
                and mode != SourceTimeAssessmentMode.ADVANCED_CORRECTION
            ):
                raise SourceTimeError(
                    SourceTimeMutationReason.SOURCE_TIME_CORRECTION_REQUIRED.value,
                    "after-cutoff sources cannot be marked eligible without advanced correction",
                )
            applied = self._artifacts.apply_source_time(
                basis,
                next_state=next_state,
                mode=mode,
                reason_code=reason,
                assessed_at=self._clock.now(),
                require_unknown=require_unknown,
                allow_resolved=allow_resolved,
            )
            source = self._evidence_graph.correct_source_time(
                project_id=basis.project_id,
                artifact_id=basis.artifact_id,
                cutoff_state=next_state,
            )
            self._evidence_graph.audit(
                basis.project_id,
                source.source_id,
                "evidence/sourceUpdated",
                {
                    "source_time_mutation": mode.value,
                    "reason_code": reason.value,
                    "cutoff_state": next_state.value,
                    "actor_id": actor_id,
                    "correction_reason": correction_reason,
                    "assessment_digest": applied.assessment_digest,
                },
            )
            return applied
