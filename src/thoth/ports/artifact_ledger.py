from __future__ import annotations

from datetime import datetime
from typing import Protocol

from thoth.domain.artifact import ArtifactEnvelope, StructuralDocument
from thoth.domain.enums import CutoffState
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.ids import ProjectId, SourceVersionId
from thoth.domain.source_time import (
    SourceTimeAssessment,
    SourceTimeAssessmentMode,
    SourceTimeMutationBasis,
    SourceTimeReasonCode,
)


class ArtifactLedgerPort(Protocol):
    def persist_ingestion(
        self,
        document: StructuralDocument,
        source_version_id: SourceVersionId,
        evidence_candidates: tuple[EvidenceSpan, ...],
    ) -> None: ...

    def read_artifact(self, artifact_id: str) -> ArtifactEnvelope | None: ...

    def list_source_versions(self, project_id: str, artifact_id: str) -> tuple[str, ...]: ...

    def read_structure(
        self, project_id: str, artifact_id: str, source_version_id: str
    ) -> StructuralDocument | None: ...

    def read_structure_metadata_digest(
        self, project_id: str, artifact_id: str, source_version_id: str
    ) -> str | None: ...

    def list_artifacts(self, project_id: ProjectId) -> tuple[ArtifactEnvelope, ...]: ...

    def list_evidence(self, project_id: ProjectId) -> tuple[EvidenceSpan, ...]: ...

    def read_evidence(self, span_id: str) -> EvidenceSpan | None: ...

    def update_evidence(self, span: EvidenceSpan) -> None: ...

    def update_artifact_cutoff(
        self,
        expected: ArtifactEnvelope,
        state: CutoffState,
    ) -> None: ...

    def read_source_time(
        self, project_id: str, artifact_id: str, source_version_id: str
    ) -> SourceTimeAssessment | None: ...

    def list_source_times(self, project_id: str) -> tuple[SourceTimeAssessment, ...]: ...

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
    ) -> SourceTimeAssessment: ...
