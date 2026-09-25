"""Keep the short-text policy separate from publication's 160-span fixture."""

import hashlib

from thoth.application.services.research_retrieval import lexical_candidates, text_neighbors
from thoth.domain.artifact import SourceLocator
from thoth.domain.enums import AuthorityState, CutoffState, SupportState, VerificationState
from thoth.domain.evidence import EvidenceSpan


def source(count: int) -> tuple[EvidenceSpan, ...]:
    return tuple(
        EvidenceSpan(
            span_id=f"span:{i}",
            project_id="p",
            artifact_id="artifact:fixture",
            source_version_id="version:fixture",
            locator=SourceLocator(line=i + 1),
            exact_text=(text := f"latency {i}: 12 ms alpha"),
            text_sha256=hashlib.sha256(text.encode()).hexdigest(),
            extraction_method="fixture",
            support_state=SupportState.EXTRACTED,
            authority_state=AuthorityState.INFORMAL,
            verification_state=VerificationState.SCHEMA_VALID,
            cutoff_state=CutoffState.ELIGIBLE,
        )
        for i in range(count)
    )


def test_mixed_short_lines_leave_sixty_anchors_with_two_original_neighbors():
    evidence = source(160)
    candidates = lexical_candidates("latency 12 ms alpha", (), evidence)
    assert {s.span_id for s in candidates} == {f"span:{i}" for i in range(100, 160)}
    expanded = {s.span_id for anchor in candidates for s in text_neighbors(anchor, evidence)}
    assert expanded == {"span:0", "span:99", *(s.span_id for s in candidates)}


def test_all_short_lines_remain_available_when_no_longer_pool_exists():
    evidence = source(10)
    candidates = lexical_candidates("latency 12 ms alpha", (), evidence)
    assert {s.span_id for s in candidates} == {s.span_id for s in evidence}
