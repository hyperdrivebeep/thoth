"""Short key-value lines reach the model with their block; web chrome stays out."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from thoth.adapters.parsers.text_parser import TextParser
from thoth.application.services.research_context_assembler import assemble_context
from thoth.application.services.research_retrieval import (
    adjacent_packet,
    lexical_candidates,
    text_neighbors,
)
from thoth.domain.artifact import ArtifactEnvelope, StructuralDocument
from thoth.domain.enums import (
    AuthorityState,
    CutoffState,
    SecurityClass,
    SupportState,
    VerificationState,
)
from thoth.domain.evidence import EvidenceSpan

DEMO = Path(__file__).parents[2] / "examples" / "synthetic-radar-demo-v1"
QUESTION = "SYN-C-DET-RAIN criterion verdict is FAIL. Find causes and tests that separate them."


def _artifact(raw: bytes, name: str, artifact_id: str) -> ArtifactEnvelope:
    return ArtifactEnvelope(
        artifact_id=artifact_id,
        project_id="project:1",
        source_uri=name,
        media_type="text/plain",
        byte_sha256=hashlib.sha256(raw).hexdigest(),
        authority=AuthorityState.INFORMAL,
        cutoff_state=CutoffState.ELIGIBLE,
        security_class=SecurityClass.INTERNAL,
        retrieved_at=datetime.now(UTC),
        parser_name="unparsed",
        parser_version="0",
    )


def _spans(document: StructuralDocument, prefix: str) -> tuple[EvidenceSpan, ...]:
    # Same construction as ingestion: the span keeps the node's locator plus its node id.
    return tuple(
        EvidenceSpan(
            span_id=f"span:{prefix}:{index}",
            project_id="project:1",
            artifact_id=document.artifact.artifact_id,
            source_version_id=f"version:{prefix}",
            locator=node.locator.model_copy(update={"structural_node_id": node.node_id}),
            exact_text=node.text or "",
            text_sha256=hashlib.sha256((node.text or "").encode()).hexdigest(),
            extraction_method="text:test",
            support_state=SupportState.EXTRACTED,
            authority_state=AuthorityState.INFORMAL,
            verification_state=VerificationState.SCHEMA_VALID,
            cutoff_state=CutoffState.ELIGIBLE,
        )
        for index, node in enumerate(document.nodes)
        if node.text
    )


def _parse(raw: bytes, name: str, artifact_id: str) -> StructuralDocument:
    return TextParser().parse(_artifact(raw, name, artifact_id), raw)


def _demo() -> tuple[StructuralDocument, tuple[EvidenceSpan, ...]]:
    path = DEMO / "31_RESULT_RAIN_SYNTHETIC.yaml"
    document = _parse(path.read_bytes(), path.name, "artifact:rain")
    return document, _spans(document, "rain")


def _by_text(spans: tuple[EvidenceSpan, ...], text: str) -> EvidenceSpan:
    return next(span for span in spans if span.exact_text == text)


class _Ledger:
    def __init__(self, document: StructuralDocument) -> None:
        self.document = document

    def read_structure(
        self, project_id: str, artifact_id: str, source_version_id: str
    ) -> StructuralDocument | None:
        return self.document


def test_neighbors_of_a_key_value_line_include_its_whole_block() -> None:
    _, spans = _demo()
    anchor = _by_text(spans, "criterion: SYN-C-DET-RAIN")
    texts = {span.exact_text for span in text_neighbors(anchor, spans)}
    assert {"detection:", "numerator: 16", "denominator: 20", "unit: ratio"} <= texts
    assert 'value: "0.80"' in texts
    # The sibling block of another key is not pulled in.
    assert "criterion: SYN-C-FA-RAIN" not in texts


def test_a_very_large_block_keeps_only_the_adjacent_lines() -> None:
    rows = "".join(f"  item_{i}: {i}\n" for i in range(40))
    document = _parse(("big:\n" + rows).encode(), "big.yaml", "artifact:big")
    spans = _spans(document, "big")
    anchor = _by_text(spans, "item_20: 20")
    texts = {span.exact_text for span in text_neighbors(anchor, spans)}
    assert texts == {"big:", "item_19: 19", "item_20: 20", "item_21: 21"}


def test_short_key_value_lines_are_candidates_when_the_question_names_them() -> None:
    _, spans = _demo()
    candidates = lexical_candidates("What was the denominator?", (), spans)
    assert "denominator: 20" in {span.exact_text for span in candidates}


def test_web_chrome_fragments_are_still_filtered() -> None:
    page = (
        b"---\n"
        b"article\n"
        b"Share\n"
        b"*   [](https://example.com/share)\n"
        b"https://www.facebook.com/sharer.php?u=1 latency link\n"
        b"The measured latency was 12 ms on the reference platform in the lab.\n"
    )
    spans = _spans(_parse(page, "page.md", "artifact:page"), "page")
    candidates = lexical_candidates("latency 12 ms share article", (), spans)
    assert {span.exact_text for span in candidates} == {
        "The measured latency was 12 ms on the reference platform in the lab."
    }


def test_the_denominator_reaches_the_model_input_for_the_rain_question() -> None:
    document, spans = _demo()
    pinned = (_by_text(spans, 'value: "0.80"').span_id,)
    candidates = lexical_candidates(QUESTION, (), spans, pinned)
    assert "denominator: 20" not in {span.exact_text for span in candidates}
    ranking = tuple(span.span_id for span in candidates)

    packet = adjacent_packet(ranking, candidates, spans, QUESTION)
    assert "denominator: 20" in {span.exact_text for span in packet}

    assembly = assemble_context(ranking, candidates, spans, _Ledger(document))  # type: ignore[arg-type]
    assert "denominator: 20" in {span.exact_text for span in assembly.evidence}
    assert _by_text(assembly.evidence, "denominator: 20").locator.line == 13
