"""Per-input scope observations, distinct from a claim of document completeness."""

from dataclasses import dataclass
from typing import Literal

from pydantic import Field

from thoth.domain.base import DomainModel
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.ids import Sha256
from thoth.domain.research_request import RevisionRef


@dataclass(frozen=True)
class RetrievalSelection:
    catalog: tuple[EvidenceSpan, ...]
    eligible: tuple[EvidenceSpan, ...]
    candidate_refs: tuple[str, ...]
    source_basis_digest: str
    policy_digest: str
    mode: Literal["FULL_ELIGIBLE_EXTRACTED_CATALOG", "LEXICAL", "LEGACY"]
    limitations: tuple[str, ...] = ()
    anchor_refs: tuple[str, ...] | None = None
    omitted_anchor_refs: tuple[str, ...] = ()
    contextual_refs: tuple[str, ...] = ()


class RetrievalCoverage(DomainModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    scope: Literal["AUTHORIZED_CONNECTED_EXTRACTED_SOURCE"] = (
        "AUTHORIZED_CONNECTED_EXTRACTED_SOURCE"
    )
    request_ref: RevisionRef
    source_basis_digest: Sha256
    policy_digest: Sha256
    role: str
    phase: Literal["CANDIDATE_PRESENTATION", "ASSEMBLED_INPUT"]
    mode: Literal["FULL_ELIGIBLE_EXTRACTED_CATALOG", "LEXICAL", "LEGACY"]
    catalog_count: int = Field(ge=0)
    eligible_count: int = Field(ge=0)
    candidate_count: int = Field(ge=0)
    input_span_count: int = Field(ge=0)
    input_span_refs: tuple[str, ...]
    ranked_anchor_count: int | None = None
    selected_count: int | None = None
    context_added_count: int | None = None
    unselected_candidate_refs: tuple[str, ...]
    omitted_anchor_refs: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    all_eligible_in_candidates: bool
    coverage_state: Literal["COMPLETE_ELIGIBLE", "PARTIAL", "UNKNOWN"]
