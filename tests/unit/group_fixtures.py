"""Shared helpers for the structure-group tests: parse a Markdown text with the real parser."""

import hashlib
from datetime import UTC, datetime
from typing import cast

from thoth.adapters.parsers.registry import default_parser_registry
from thoth.application.services import ingestion_service
from thoth.application.services.research_selection_context import (
    CandidateContext,
    candidate_context,
)
from thoth.domain.artifact import ArtifactEnvelope, StructuralDocument
from thoth.domain.enums import (
    AuthorityState,
    CutoffState,
    SecurityClass,
    SupportState,
    VerificationState,
)
from thoth.domain.evidence import EvidenceSpan, connected_retrieval_spans
from thoth.ports.artifact_ledger import ArtifactLedgerPort


class Reader:
    def __init__(self) -> None:
        self.docs: dict[tuple[str, str], StructuralDocument] = {}

    def read_structure(self, project: str, artifact: str, version: str) -> StructuralDocument:
        return self.docs[(artifact, version)]


def parse(markdown: str, name: str, reader: Reader, *, start: int = 0) -> tuple[EvidenceSpan, ...]:
    """The spans the ingestion keeps from one Markdown source, with ids ${name}:NNNN."""
    raw = markdown.encode()
    art = ArtifactEnvelope(
        artifact_id=f"artifact:{name}",
        project_id="project:g3",
        source_uri=f"{name}.md",
        media_type="text/markdown",
        byte_sha256=hashlib.sha256(raw).hexdigest(),
        authority=AuthorityState.INFORMAL,
        cutoff_state=CutoffState.ELIGIBLE,
        security_class=SecurityClass.INTERNAL,
        retrieved_at=datetime(2026, 10, 26, tzinfo=UTC),
        parser_name="unparsed",
        parser_version="0",
    )
    doc = default_parser_registry().parse(art, raw, source_path=None)
    version = f"version:{name}"
    assert doc.context_observation is not None
    doc = doc.model_copy(update={"context_observation": doc.context_observation.bind(version)})
    reader.docs[(doc.artifact.artifact_id, version)] = doc
    kinds = cast(frozenset[object], vars(ingestion_service)["_EVIDENCE_NODE_KINDS"])
    nodes = [(n, n.text) for n in doc.nodes if n.kind in kinds and n.text]
    return tuple(
        EvidenceSpan(
            span_id=f"span:{name}:{i:04d}",
            project_id="project:g3",
            artifact_id=doc.artifact.artifact_id,
            source_version_id=version,
            locator=n.locator.model_copy(update={"structural_node_id": n.node_id}),
            exact_text=text,
            text_sha256=hashlib.sha256(text.encode()).hexdigest(),
            extraction_method="text:1.2.0",
            support_state=SupportState.EXTRACTED,
            authority_state=AuthorityState.INFORMAL,
            verification_state=VerificationState.SCHEMA_VALID,
            cutoff_state=CutoffState.ELIGIBLE,
        )
        for i, (n, text) in enumerate(nodes, start=start)
    )


def context(spans: tuple[EvidenceSpan, ...], reader: Reader) -> CandidateContext:
    return candidate_context(connected_retrieval_spans(spans), cast(ArtifactLedgerPort, reader))
