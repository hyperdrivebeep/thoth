from __future__ import annotations

from pydantic import model_validator

from thoth.domain.base import DomainModel
from thoth.domain.enums import (
    AuthorityState,
    CutoffState,
    SupportState,
    VerificationState,
)
from thoth.domain.ids import ClaimId, DecisionObjectId, EvidenceSpanId, ProjectId


class Claim(DomainModel):
    claim_id: ClaimId
    project_id: ProjectId
    object_id: DecisionObjectId
    text: str
    evidence_refs: tuple[EvidenceSpanId, ...]
    support_state: SupportState
    authority_state: AuthorityState
    verification_state: VerificationState
    cutoff_state: CutoffState
    uncertainty: str
    schema_version: str = "1.0.0"

    @model_validator(mode="after")
    def unsupported_claim_cannot_look_verified(self) -> Claim:
        if not self.evidence_refs and self.support_state not in {
            SupportState.DISCOVERED,
            SupportState.UNRESOLVED,
        }:
            raise ValueError("claim without evidence must remain discovered or unresolved")
        return self
