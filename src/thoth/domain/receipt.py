from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, model_validator

from thoth.domain.base import DomainModel
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.enums import (
    IntegrityState,
    ProvenanceState,
    ReceiptClaimScope,
    ReceiptType,
    SignatureState,
    TimestampTrust,
)
from thoth.domain.ids import ProjectId, ReceiptId, Sha256


class Receipt(DomainModel):
    receipt_id: ReceiptId
    project_id: ProjectId
    receipt_type: ReceiptType
    claim_scopes: tuple[ReceiptClaimScope, ...]
    subject_refs: tuple[str, ...]
    evidence_refs: tuple[str, ...] = ()
    before_head_set_digest: Sha256
    after_head_set_digest: Sha256
    previous_transition_digest: Sha256 | None = None
    parent_receipt_refs: tuple[ReceiptId, ...] = ()
    policy_version: str
    schema_version: str = "1.0.0"
    model_versions: tuple[str, ...] = ()
    tool_versions: tuple[str, ...] = ()
    integrity_state: IntegrityState
    provenance_state: ProvenanceState
    signature_state: SignatureState
    timestamp_trust: TimestampTrust
    recorded_at: AwareDatetime
    receipt_digest: Sha256
    actor_id: str | None = None
    session_id: str | None = None
    role_assignment_ref: str | None = None
    semantic_truth_certified: Literal[False] = False

    @model_validator(mode="after")
    def require_claim_scope(self) -> Receipt:
        if not self.claim_scopes:
            raise ValueError("receipt requires at least one claim scope")
        return self


def calculate_receipt_digest(receipt: Receipt) -> Sha256:
    payload: dict[str, object] = {
        "receipt_id": receipt.receipt_id,
        "project_id": receipt.project_id,
        "receipt_type": receipt.receipt_type.value,
        "claim_scopes": [scope.value for scope in receipt.claim_scopes],
        "subject_refs": list(receipt.subject_refs),
        "before_head_set_digest": receipt.before_head_set_digest,
        "after_head_set_digest": receipt.after_head_set_digest,
        "parent_receipt_refs": list(receipt.parent_receipt_refs),
        "policy_version": receipt.policy_version,
        "schema_version": receipt.schema_version,
        "model_versions": list(receipt.model_versions),
        "tool_versions": list(receipt.tool_versions),
        "integrity_state": receipt.integrity_state.value,
        "provenance_state": receipt.provenance_state.value,
        "signature_state": receipt.signature_state.value,
        "timestamp_trust": receipt.timestamp_trust.value,
        "recorded_at": receipt.recorded_at,
        "actor_id": receipt.actor_id,
        "session_id": receipt.session_id,
        "role_assignment_ref": receipt.role_assignment_ref,
        "semantic_truth_certified": receipt.semantic_truth_certified,
    }
    # Empty evidence preserves legacy producer digests and stored receipts.
    if receipt.evidence_refs:
        payload["evidence_refs"] = list(receipt.evidence_refs)
    if receipt.previous_transition_digest is not None:
        payload["previous_transition_digest"] = receipt.previous_transition_digest
    return domain_digest("RECEIPT", receipt.schema_version, canonical_payload(payload))
