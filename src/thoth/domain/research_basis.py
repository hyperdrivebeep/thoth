"""Exact, versioned research provenance; current heads cannot fill historical gaps."""

from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from thoth.domain.base import DomainModel
from thoth.domain.ids import Sha256
from thoth.domain.research_reference import RevisionRef


class SourceBasisRef(DomainModel):
    artifact_id: str
    source_version_id: str
    span_id: str | None = None
    text_sha256: str | None = None
    binding_digest: str | None = None
    grant_digest: str | None = None


class ResearchResultBasis(DomainModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    request_ref: RevisionRef
    consumed_heads: dict[str, Sha256] = Field(default_factory=dict)
    produced_refs: tuple[RevisionRef, ...] = ()
    produced_final_heads: dict[str, Sha256] = Field(default_factory=dict)
    producer_receipt_refs: tuple[str, ...] = ()
    source_basis: tuple[SourceBasisRef, ...] = ()
    memory_revision_refs: tuple[Sha256, ...] = ()
    object_refs: tuple[RevisionRef, ...] = ()
    policy_digest: Sha256
    cutoff_at: AwareDatetime
    coverage: Literal["COMPLETE", "PARTIAL", "UNKNOWN"] = "UNKNOWN"
    reasons: tuple[str, ...] = ()

    @model_validator(mode="after")
    def require_final_producers(self) -> "ResearchResultBasis":
        produced = {
            (f"{ref.entity_type}:{ref.entity_id}", ref.revision_digest)
            for ref in self.produced_refs
            if ref.project_id == self.request_ref.project_id
        }
        if not set(self.produced_final_heads.items()).issubset(produced):
            raise ValueError("BASIS_FINAL_PRODUCER_UNRESOLVED")
        if any(ref.project_id != self.request_ref.project_id for ref in self.produced_refs):
            raise ValueError("BASIS_PRODUCER_PROJECT_MISMATCH")
        return self

    @property
    def effective_expected_heads(self) -> dict[str, Sha256]:
        return {**self.consumed_heads, **self.produced_final_heads}


class BasisCurrentness(DomainModel):
    state: Literal["CURRENT", "REVIEW_REQUIRED", "INVALIDATED", "UNKNOWN_BASIS", "UNAVAILABLE"]
    reasons: tuple[str, ...] = ()
    execution_eligible: bool = False
