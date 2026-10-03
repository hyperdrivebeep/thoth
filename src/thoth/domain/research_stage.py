"""Completed typed role outputs kept in the existing research revision owner."""

from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, model_validator

from thoth.domain.base import DomainModel
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.evidence_requirements import (
    EvidenceRanking,
    RequirementProposal,
    ResearchSourcePlan,
    ReviewAdjudication,
    ReviewProposal,
)
from thoth.domain.model_settings import ResolvedModelSettings
from thoth.domain.research_request import RevisionRef

OUTPUT_CODECS = {
    c.__name__: c
    for c in (
        RequirementProposal,
        EvidenceRanking,
        ReviewProposal,
        ReviewAdjudication,
        ResearchSourcePlan,
    )
}


class ReusedStageOrigin(DomainModel):
    """Where a reused stage's output was first produced; a reuse is never a new model call."""

    stage_ref: RevisionRef
    operation_id: str


class ResearchStageRecord(DomainModel):
    record_kind: Literal["ResearchStageRecord"] = "ResearchStageRecord"
    schema_version: Literal["2.0.0"] = "2.0.0"
    request_ref: RevisionRef
    operation_id: str
    role: str
    prompt_version: str
    output_codec: str
    output_schema_digest: str
    input_basis_digest: str
    provider_input_digest: str
    provider_output_digest: str
    output_payload_digest: str
    # The same basis without the request revision: equal only for the same question, context,
    # role, prompt, schema, model settings, cutoff, policy and behavior. Absent in older records,
    # which are therefore never reused.
    reuse_basis_digest: str | None = None
    reused_from: ReusedStageOrigin | None = None
    output: dict[str, object]
    model_id: str
    model_settings: ResolvedModelSettings | None
    source_versions: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    behavior_basis: tuple[str, ...]
    dispatch_ids: tuple[str, ...] = ()
    context_bytes: int
    elapsed_ms: int
    completed_at: AwareDatetime
    state: Literal["COMPLETED"] = "COMPLETED"

    @model_validator(mode="after")
    def completed_typed_output(self) -> ResearchStageRecord:
        codec = OUTPUT_CODECS.get(self.output_codec)
        if codec is None:
            raise ValueError("STAGE_OUTPUT_CODEC_UNSUPPORTED")
        codec.model_validate(self.output)
        if self.reused_from is not None and self.dispatch_ids:
            raise ValueError("REUSED_STAGE_HAS_NO_DISPATCH")
        if self.output_payload_digest != domain_digest(
            "RESEARCH_STAGE_OUTPUT", "2.0.0", canonical_payload(self.output)
        ):
            raise ValueError("STAGE_OUTPUT_DIGEST_MISMATCH")
        return self
