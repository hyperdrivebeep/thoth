from __future__ import annotations

from pydantic import AwareDatetime

from thoth.domain.artifact import SourceLocator
from thoth.domain.base import DomainModel
from thoth.domain.enums import (
    AuthorityState,
    CutoffState,
    DimensionStatus,
    SufficiencyStatus,
    SupportState,
    VerificationState,
)
from thoth.domain.ids import (
    ArtifactId,
    DecisionObjectId,
    EvidenceSpanId,
    ProjectId,
    RevisionId,
    Sha256,
    SourceVersionId,
)


class EvidenceSpan(DomainModel):
    span_id: EvidenceSpanId
    project_id: ProjectId
    artifact_id: ArtifactId
    source_version_id: SourceVersionId
    locator: SourceLocator
    exact_text: str
    text_sha256: Sha256
    extraction_method: str
    support_state: SupportState
    authority_state: AuthorityState
    verification_state: VerificationState
    cutoff_state: CutoffState
    schema_version: str = "1.0.0"


def connected_retrieval_spans(evidence: tuple[EvidenceSpan, ...]) -> tuple[EvidenceSpan, ...]:
    """Prefer cutoff-eligible connected spans; if none exist, read unconfirmed times.

    After-cutoff and prohibited context stay out of retrieval. Unknown time is
    used only when the connected catalog has no eligible span, so a user-attached
    record is not invisible to live research.
    """
    usable = tuple(
        span
        for span in evidence
        if span.authority_state != AuthorityState.NOT_ADMISSIBLE
        and span.cutoff_state
        not in {CutoffState.AFTER_CUTOFF, CutoffState.PROHIBITED_CONTEXT}
    )
    eligible = tuple(span for span in usable if span.cutoff_state == CutoffState.ELIGIBLE)
    return eligible or tuple(
        span for span in usable if span.cutoff_state == CutoffState.UNKNOWN_TIME
    )


class SufficiencyDimension(DomainModel):
    status: DimensionStatus
    evidence_refs: tuple[EvidenceSpanId, ...] = ()
    reason: str


class InformationSufficiencyAssessment(DomainModel):
    assessment_id: str
    assessment_revision_id: RevisionId
    project_id: ProjectId
    target_object_id: DecisionObjectId
    cutoff_at: AwareDatetime
    decision_question: str
    scope_identity: SufficiencyDimension
    criterion_authority: SufficiencyDimension
    evidence_coverage: SufficiencyDimension
    comparability: SufficiencyDimension
    counterevidence: SufficiencyDimension
    instrumentation: SufficiencyDimension
    expert_semantics: SufficiencyDimension
    derived_status: tuple[SufficiencyStatus, ...]
    missing_items: tuple[str, ...] = ()
    next_queries: tuple[str, ...] = ()
    policy_version: str
    input_head_set_digest: Sha256
    schema_version: str = "1.0.0"
