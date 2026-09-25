"""Shared selection and publication contracts for every semantic restore entry."""

from typing import Literal

from pydantic import Field

from thoth.domain.base import DomainModel
from thoth.domain.enums import EntityType
from thoth.domain.ids import Sha256
from thoth.domain.research_history import HistoryCapability, SemanticChangeGroup
from thoth.domain.revision import ImpactPropagationPlan, SemanticDiffEntry


class RestoreSelection(DomainModel):
    project_id: str
    entity_type: EntityType
    entity_id: str
    target_revision_digest: Sha256
    expected_current_head: Sha256


class RestoreReferenceBasis(DomainModel):
    expected_heads: dict[str, Sha256] = Field(default_factory=dict)
    records: dict[str, dict[str, object]] = Field(default_factory=dict)


class RestorePreviewInput(DomainModel):
    project_id: str
    selection: RestoreSelection
    contract_version: Literal[2] = 2


class RestoreApplyInput(RestorePreviewInput):
    preview_basis_digest: Sha256
    reason: str = Field(min_length=1, max_length=2000)


class RestoreBasis(DomainModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    selection: RestoreSelection
    profile_id: str
    target_content_digest: Sha256
    expected_heads: dict[str, Sha256]
    dependency_states: dict[str, str]
    source_basis: dict[str, object]
    policy_basis: dict[str, object]
    authority_basis: dict[str, object]
    consumer_basis: dict[str, object]
    impact: ImpactPropagationPlan
    impact_complete: bool


class RestorePreview(DomainModel):
    contract_version: Literal[2] = 2
    selection: RestoreSelection
    profile_id: str | None = None
    availability: Literal["AVAILABLE", "BLOCKED", "NO_CHANGE"]
    reason_codes: tuple[str, ...] = ()
    capability: HistoryCapability
    actor_scope_digest: Sha256
    basis_digest: Sha256 | None = None
    impact: ImpactPropagationPlan = Field(default_factory=ImpactPropagationPlan)
    diff: tuple[SemanticDiffEntry, ...] = ()
    summary_groups: tuple[SemanticChangeGroup, ...] = ()
    source_drift: tuple[str, ...] = ()
    reanalysis: Literal["NOT_REQUESTED"] = "NOT_REQUESTED"
    external_effects: Literal["PRESERVED"] = "PRESERVED"


class RestoreApplyResult(DomainModel):
    contract_version: Literal[2] = 2
    status: Literal["APPLIED", "NO_CHANGE"]
    selection: RestoreSelection
    new_revision_digest: Sha256 | None = None
    new_revision_id: str | None = None
    receipt_id: str | None = None
    receipt_digest: Sha256 | None = None
    restore_audit_id: str | None = None
    impact: ImpactPropagationPlan
    reanalysis: Literal["NOT_REQUESTED"] = "NOT_REQUESTED"
    external_effects: Literal["PRESERVED"] = "PRESERVED"


class RestoreReceiptV1(DomainModel):
    record_kind: Literal["RestoreReceipt"] = "RestoreReceipt"
    schema_version: Literal["1.0.0"] = "1.0.0"
    operation_id: str
    basis: RestoreBasis
    preview_basis_digest: Sha256
    result: RestoreApplyResult
    parent_revision_digests: tuple[Sha256, ...] = ()


class RestoreProposalV1(DomainModel):
    record_kind: Literal["RestoreProposal"] = "RestoreProposal"
    schema_version: Literal["1.0.0"] = "1.0.0"
    selection: RestoreSelection
    preview_basis_digest: Sha256
    reason: str
    profile_id: str
    impact: ImpactPropagationPlan
    restores_revision_digest: Sha256
    parent_revision_digests: tuple[Sha256, ...]


def decode_restore_record(payload: dict[str, object]) -> RestoreReceiptV1 | RestoreProposalV1:
    if payload.get("schema_version") != "1.0.0":
        raise ValueError("RESTORE_CODEC_VERSION_UNSUPPORTED")
    if payload.get("record_kind") == "RestoreReceipt":
        return RestoreReceiptV1.model_validate(payload)
    if payload.get("record_kind") == "RestoreProposal":
        return RestoreProposalV1.model_validate(payload)
    raise ValueError("RESTORE_CODEC_KIND_UNSUPPORTED")


class RestoreError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)
