"""AI-proposed gap meaning with independently validated requirement/source bindings."""

from typing import Literal

from thoth.domain.base import DomainModel
from thoth.domain.research_request import RevisionRef

Disposition = Literal["RESOLVED", "NOT_REQUIRED", "UNRESOLVED"]


class GapProposal(DomainModel):
    gap_id: str
    omission_text: str
    requirement_id: str | None = None
    anchor_span_refs: tuple[str, ...] = ()
    proposed_disposition: Disposition = "UNRESOLVED"
    proposed_span_refs: tuple[str, ...] = ()
    proposed_structure_refs: tuple[str, ...] = ()
    source_version_ids: tuple[str, ...] = ()
    required_relation_kinds: tuple[str, ...] = ()
    explanation: str


class GapReviewDecision(DomainModel):
    gap_id: str
    verdict: Literal["APPLIED", "REJECTED", "INCONCLUSIVE"]
    disposition: Disposition
    requirement_set_digest: str
    basis_span_refs: tuple[str, ...] = ()
    relations_confirmed: bool = False
    explanation: str


class GapTarget(DomainModel):
    gap_id: str
    omission_text: str
    requirement_id: str | None
    anchor_span_refs: tuple[str, ...] = ()
    required_relation_kinds: tuple[str, ...] = ()
    origin: Literal["RERANKER", "LEGACY_RERANKER", "SEMANTIC_REVIEW", "STRUCTURE_OBSERVATION"]


class GapValidation(DomainModel):
    request_ref: RevisionRef
    requirement_set_ref: RevisionRef
    gap_id: str
    requirement_id: str | None
    effective_disposition: Disposition
    structural_result: Literal["MATCH", "MISSING", "INVALID"]
    semantic_result: Literal["REVIEWED_APPLIED", "REVIEWED_REJECTED", "INCONCLUSIVE"]
    span_digests: dict[str, str]
    source_versions: dict[str, str]
    structure_digests: dict[str, str]
    relation_bases: tuple[str, ...]
    reasons: tuple[str, ...]
    reviewer_run: str
