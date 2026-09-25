"""Captured evidence inputs; historical artifacts remain distinct from newer artifacts."""

from pydantic import Field, model_validator

from thoth.domain.artifact import ArtifactEnvelope
from thoth.domain.base import DomainModel
from thoth.domain.canonical import model_digest
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.evidence_graph import EvidenceSourceRecord


class EvidenceSourceBasis(DomainModel):
    project_id: str
    artifact_id: str
    artifact_digest: str
    source_id: str
    source_digest: str
    source_state_digest: str


class EvidenceSpanBasis(DomainModel):
    project_id: str
    artifact_id: str
    span_id: str
    source_version_id: str
    span_digest: str


class EvidenceCommitBasis(DomainModel):
    project_id: str
    sources: tuple[EvidenceSourceBasis, ...] = Field(min_length=1)
    spans: tuple[EvidenceSpanBasis, ...] = Field(min_length=1)
    predecessor_id: str | None = None
    predecessor_digest: str | None = None

    @model_validator(mode="after")
    def same_project_and_artifacts(self) -> "EvidenceCommitBasis":
        if any(row.project_id != self.project_id for row in (*self.sources, *self.spans)):
            raise ValueError("EVIDENCE_BASIS_PROJECT_MISMATCH")
        artifacts = {row.artifact_id for row in self.sources}
        if len(artifacts) != len(self.sources) or artifacts != {
            row.artifact_id for row in self.spans
        }:
            raise ValueError("EVIDENCE_BASIS_ARTIFACT_MISMATCH")
        if len({row.span_id for row in self.spans}) != len(self.spans):
            raise ValueError("EVIDENCE_BASIS_DUPLICATE_SPAN")
        if (self.predecessor_id is None) != (self.predecessor_digest is None):
            raise ValueError("EVIDENCE_BASIS_PREDECESSOR_REQUIRED")
        return self


def source_basis(artifact: ArtifactEnvelope, source: EvidenceSourceRecord) -> EvidenceSourceBasis:
    if (artifact.project_id, artifact.artifact_id) != (source.project_id, source.artifact_id):
        raise ValueError("EVIDENCE_BASIS_SOURCE_ARTIFACT_MISMATCH")
    return EvidenceSourceBasis(
        project_id=artifact.project_id,
        artifact_id=artifact.artifact_id,
        artifact_digest=model_digest("EVIDENCE_ARTIFACT_BASIS", artifact, schema_version="1.0.0"),
        source_id=source.source_id,
        source_digest=source.source_digest,
        source_state_digest=model_digest("EVIDENCE_SOURCE_BASIS", source, schema_version="1.0.0"),
    )


def span_basis(span: EvidenceSpan) -> EvidenceSpanBasis:
    return EvidenceSpanBasis(
        project_id=span.project_id,
        artifact_id=span.artifact_id,
        span_id=span.span_id,
        source_version_id=span.source_version_id,
        span_digest=model_digest("EVIDENCE_SPAN_BASIS", span, schema_version="1.0.0"),
    )
