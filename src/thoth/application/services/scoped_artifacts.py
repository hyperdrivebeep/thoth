"""User-facing artifact reads enforce current scope; raw metadata remains adapter-owned."""

from datetime import datetime

from thoth.application.services.resource_scope_context import current_resource_stage
from thoth.application.services.resource_scope_service import ResourceScopeService
from thoth.domain.artifact import ArtifactEnvelope, StructuralDocument
from thoth.domain.enums import CutoffState
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.ids import ProjectId, SourceVersionId
from thoth.domain.resource_scope import ResourceScopeError
from thoth.domain.source_time import (
    SourceTimeAssessment,
    SourceTimeAssessmentMode,
    SourceTimeMutationBasis,
    SourceTimeReasonCode,
)
from thoth.ports.artifact_ledger import ArtifactLedgerPort
from thoth.ports.ledger import LedgerPort


class ScopedArtifactLedger(ArtifactLedgerPort):
    def __init__(
        self, raw: ArtifactLedgerPort, scopes: ResourceScopeService, ledger: LedgerPort
    ) -> None:
        self.raw = raw
        self.scopes = scopes
        self._ledger = ledger

    def persist_ingestion(
        self,
        document: StructuralDocument,
        source_version_id: SourceVersionId,
        evidence_candidates: tuple[EvidenceSpan, ...],
    ) -> None:
        staged = current_resource_stage()
        if staged is None or staged.record.resource_ref != document.artifact.artifact_id:
            raise ResourceScopeError("RESOURCE_INTAKE_RECORD_MISMATCH")
        with self._ledger.transaction():
            self.scopes.admit(staged)
            self.raw.persist_ingestion(document, source_version_id, evidence_candidates)

    def read_artifact(self, artifact_id: str) -> ArtifactEnvelope | None:
        value = self.raw.read_artifact(artifact_id)
        if value is not None:
            self.scopes.require_read(value.project_id, artifact_id)
        return value

    def read_evidence(self, span_id: str) -> EvidenceSpan | None:
        value = self.raw.read_evidence(span_id)
        if value is not None:
            self.scopes.require_read(value.project_id, span_id)
        return value

    def list_source_versions(self, project_id: str, artifact_id: str) -> tuple[str, ...]:
        artifact = self.read_artifact(artifact_id)
        if artifact is None:
            return ()
        if artifact.project_id != project_id:
            raise ResourceScopeError("RESOURCE_SCOPE_PROJECT_MISMATCH")
        return self.raw.list_source_versions(project_id, artifact_id)

    def read_structure(
        self, project_id: str, artifact_id: str, source_version_id: str
    ) -> StructuralDocument | None:
        with self._ledger.transaction():
            artifact = self.raw.read_artifact(artifact_id)
            if artifact is None:
                return None
            if artifact.project_id != project_id:
                raise ResourceScopeError("RESOURCE_SCOPE_PROJECT_MISMATCH")
            self.scopes.require_read(project_id, artifact_id)
            return self.raw.read_structure(project_id, artifact_id, source_version_id)

    def read_structure_metadata_digest(
        self, project_id: str, artifact_id: str, source_version_id: str
    ) -> str | None:
        with self._ledger.transaction():
            artifact = self.raw.read_artifact(artifact_id)
            if artifact is None:
                return None
            if artifact.project_id != project_id:
                raise ResourceScopeError("RESOURCE_SCOPE_PROJECT_MISMATCH")
            self.scopes.require_read(project_id, artifact_id)
            return self.raw.read_structure_metadata_digest(
                project_id, artifact_id, source_version_id
            )

    def list_artifacts(self, project_id: ProjectId) -> tuple[ArtifactEnvelope, ...]:
        return tuple(
            value
            for value in self.raw.list_artifacts(project_id)
            if self.scopes.may_read(project_id, value.artifact_id)
        )

    def list_evidence(self, project_id: ProjectId) -> tuple[EvidenceSpan, ...]:
        allowed = {value.artifact_id for value in self.list_artifacts(project_id)}
        return tuple(
            value for value in self.raw.list_evidence(project_id) if value.artifact_id in allowed
        )

    def update_evidence(self, span: EvidenceSpan) -> None:
        self.scopes.require_write(span.project_id, span.artifact_id)
        self.raw.update_evidence(span)

    def update_artifact_cutoff(self, expected: ArtifactEnvelope, state: CutoffState) -> None:
        self.scopes.require_write(expected.project_id, expected.artifact_id)
        self.raw.update_artifact_cutoff(expected, state)

    def read_source_time(
        self, project_id: str, artifact_id: str, source_version_id: str
    ) -> SourceTimeAssessment | None:
        self.scopes.require_read(project_id, artifact_id)
        return self.raw.read_source_time(project_id, artifact_id, source_version_id)

    def list_source_times(self, project_id: str) -> tuple[SourceTimeAssessment, ...]:
        allowed = {value.artifact_id for value in self.list_artifacts(project_id)}
        return tuple(
            item for item in self.raw.list_source_times(project_id) if item.artifact_id in allowed
        )

    def apply_source_time(
        self,
        basis: SourceTimeMutationBasis,
        *,
        next_state: CutoffState,
        mode: SourceTimeAssessmentMode,
        reason_code: SourceTimeReasonCode,
        assessed_at: datetime,
        require_unknown: bool,
        allow_resolved: bool,
    ) -> SourceTimeAssessment:
        self.scopes.require_write(basis.project_id, basis.artifact_id)
        with self._ledger.transaction():
            return self.raw.apply_source_time(
                basis,
                next_state=next_state,
                mode=mode,
                reason_code=reason_code,
                assessed_at=assessed_at,
                require_unknown=require_unknown,
                allow_resolved=allow_resolved,
            )
