from thoth.domain.canonical import model_digest
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.evidence_basis import (
    EvidenceCommitBasis,
    EvidenceSourceBasis,
    source_basis,
    span_basis,
)
from thoth.domain.evidence_graph import EvidenceLinkRecord, EvidenceSourceRecord
from thoth.ports.artifact_ledger import ArtifactLedgerPort
from thoth.ports.evidence_graph import EvidenceGraphStorePort


def capture_basis(
    project: str,
    spans: tuple[EvidenceSpan, ...],
    sources: tuple[EvidenceSourceRecord, ...],
    artifacts: ArtifactLedgerPort,
    predecessor: EvidenceLinkRecord | None,
) -> EvidenceCommitBasis:
    selected = {source.artifact_id: source for source in sources}
    if len({source.source_id for source in sources}) != len(selected):
        raise ValueError("EVIDENCE_SOURCE_REVISION_CONFLICT")
    bindings: list[EvidenceSourceBasis] = []
    for artifact_id, source in selected.items():
        artifact = artifacts.read_artifact(artifact_id)
        if artifact is None:
            raise ValueError("EVIDENCE_ARTIFACT_REVISION_CONFLICT")
        bindings.append(source_basis(artifact, source))
    return EvidenceCommitBasis(
        project_id=project,
        sources=tuple(bindings),
        spans=tuple(span_basis(span) for span in {s.span_id: s for s in spans}.values()),
        predecessor_id=None if predecessor is None else predecessor.evidence_id,
        predecessor_digest=None
        if predecessor is None
        else model_digest("EVIDENCE_LINK_BASIS", predecessor, schema_version="1.0.0"),
    )


def require_same_sources(
    basis: EvidenceCommitBasis, artifacts: ArtifactLedgerPort, store: EvidenceGraphStorePort
) -> None:
    for expected in basis.sources:
        artifact = artifacts.read_artifact(expected.artifact_id)
        source = store.read_source_by_artifact(expected.artifact_id)
        if artifact is None or source is None or source_basis(artifact, source) != expected:
            raise ValueError("EVIDENCE_SOURCE_REVISION_CONFLICT")
    for expected in basis.spans:
        span = artifacts.read_evidence(expected.span_id)
        if span is None or span_basis(span) != expected:
            raise ValueError("EVIDENCE_SPAN_REVISION_CONFLICT")
