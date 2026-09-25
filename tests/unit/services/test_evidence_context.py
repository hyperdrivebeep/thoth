from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import cast

import pytest

from thoth.application.services import select_evidence_context
from thoth.application.services.research_context_assembler import assemble_context
from thoth.domain.artifact import (
    ArtifactEnvelope,
    SourceLocator,
    StructuralDocument,
    StructuralNode,
    StructuralRelation,
)
from thoth.domain.enums import (
    AuthorityState,
    CutoffState,
    SecurityClass,
    StructuralNodeKind,
    SupportState,
    VerificationState,
)
from thoth.domain.evidence import EvidenceSpan
from thoth.ports.artifact_ledger import ArtifactLedgerPort


def _span(identifier: str, text: str, *, artifact: str = "artifact:1") -> EvidenceSpan:
    return EvidenceSpan(
        span_id=identifier,
        project_id="project:1",
        artifact_id=artifact,
        source_version_id=f"source-version:{artifact}",
        locator=SourceLocator(page=1),
        exact_text=text,
        text_sha256=(identifier[-1] * 64)[:64],
        extraction_method="fixture",
        support_state=SupportState.EXTRACTED,
        authority_state=AuthorityState.OFFICIAL,
        verification_state=VerificationState.PROVENANCE_VALID,
        cutoff_state=CutoffState.ELIGIBLE,
    )


def _structured_span(
    identifier: str,
    text: str,
    node_id: str,
    *,
    source_version_id: str = "source-version:continuation",
) -> EvidenceSpan:
    return EvidenceSpan(
        span_id=identifier,
        project_id="project:1",
        artifact_id="artifact:continuation",
        source_version_id=source_version_id,
        locator=SourceLocator(page=1, structural_node_id=node_id),
        exact_text=text,
        text_sha256=hashlib.sha256(text.encode()).hexdigest(),
        extraction_method="fixture",
        support_state=SupportState.EXTRACTED,
        authority_state=AuthorityState.OFFICIAL,
        verification_state=VerificationState.PROVENANCE_VALID,
        cutoff_state=CutoffState.ELIGIBLE,
    )


class _Ledger:
    def __init__(self, document: StructuralDocument) -> None:
        self.document = document

    def read_structure(
        self, project_id: str, artifact_id: str, source_version_id: str
    ) -> StructuralDocument | None:
        if (project_id, artifact_id, source_version_id) == (
            "project:1",
            "artifact:continuation",
            "source-version:continuation",
        ):
            return self.document
        return None


def test_selector_forces_criterion_span_and_prefers_problem_relevance() -> None:
    evidence = (
        _span("span:1", "unrelated dissemination text"),
        _span("span:2", "virtualization configuration changed the latency baseline"),
        _span("span:3", "official criterion target below one millisecond", artifact="artifact:2"),
    )

    result = select_evidence_context(
        problem="Why did virtualization configuration change latency?",
        evidence=evidence,
        forced_refs=("span:3",),
        max_spans=2,
        character_budget=1_000,
    )

    assert tuple(span.span_id for span in result.selected) == ("span:3", "span:2")
    assert result.excluded_count == 1


def test_selector_rejects_missing_forced_reference() -> None:
    with pytest.raises(ValueError, match="forced evidence references are missing"):
        select_evidence_context(
            problem="latency",
            evidence=(_span("span:1", "latency"),),
            forced_refs=("span:missing",),
        )


def test_context_assembler_expands_same_version_continuation_group() -> None:
    artifact = ArtifactEnvelope(
        artifact_id="artifact:continuation",
        project_id="project:1",
        source_uri="fixture.pdf",
        media_type="application/pdf",
        byte_sha256="0" * 64,
        authority=AuthorityState.OFFICIAL,
        cutoff_state=CutoffState.ELIGIBLE,
        security_class=SecurityClass.INTERNAL,
        retrieved_at=datetime.now(UTC),
        parser_name="fixture",
        parser_version="1.2.0",
    )
    nodes = (
        StructuralNode(
            node_id="node:root",
            artifact_id=artifact.artifact_id,
            kind=StructuralNodeKind.SECTION,
            ordinal=0,
            locator=SourceLocator(page=1),
        ),
        StructuralNode(
            node_id="node:first",
            artifact_id=artifact.artifact_id,
            kind=StructuralNodeKind.PARAGRAPH,
            parent_id="node:root",
            ordinal=1,
            text="first continuation span",
            locator=SourceLocator(page=1, structural_node_id="node:first"),
        ),
        StructuralNode(
            node_id="node:middle",
            artifact_id=artifact.artifact_id,
            kind=StructuralNodeKind.PARAGRAPH,
            parent_id="node:root",
            ordinal=2,
            text="middle continuation span",
            locator=SourceLocator(page=2, structural_node_id="node:middle"),
            relations=(
                StructuralRelation(kind="CONTINUATION_OF", target_id="node:first"),
            ),
        ),
        StructuralNode(
            node_id="node:last",
            artifact_id=artifact.artifact_id,
            kind=StructuralNodeKind.PARAGRAPH,
            parent_id="node:root",
            ordinal=3,
            text="last continuation span",
            locator=SourceLocator(page=3, structural_node_id="node:last"),
            relations=(StructuralRelation(kind="CONTINUATION_OF", target_id="node:first"),),
        ),
    )
    document = StructuralDocument(
        artifact=artifact,
        nodes=nodes,
        extraction_coverage="OBSERVED_STRUCTURE",
    )
    spans = (
        _structured_span("span:first", "first continuation span", "node:first"),
        _structured_span("span:middle", "middle continuation span", "node:middle"),
        _structured_span("span:last", "last continuation span", "node:last"),
    )

    assembly = assemble_context(
        ranking=("span:middle",),
        candidates=(spans[1],),
        source=spans,
        artifacts=cast(ArtifactLedgerPort, _Ledger(document)),
    )

    assert {span.span_id for span in assembly.evidence} == {
        "span:first",
        "span:middle",
        "span:last",
    }
    assert len(assembly.bundles) == 1
    assert assembly.bundles[0].container_id == "node:first"
    assert {ref.node_id for ref in assembly.bundles[0].structures} == {
        "node:first",
        "node:middle",
        "node:last",
    }
    assert any(
        relation.kind == "CONTINUATION_OF" and relation.target_id == "node:first"
        for ref in assembly.bundles[0].structures
        for relation in ref.relations
    )
