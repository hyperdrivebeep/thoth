from __future__ import annotations

from enum import StrEnum

from pydantic import AwareDatetime

from thoth.domain.actor import ActorRef
from thoth.domain.base import DomainModel
from thoth.domain.canonical import canonical_payload, domain_digest, head_set_digest
from thoth.domain.enums import (
    IntegrityState,
    ProvenanceState,
    ReceiptClaimScope,
    ReceiptType,
    SignatureState,
    TimestampTrust,
)
from thoth.domain.ids import ProjectId, RevisionId, Sha256
from thoth.domain.receipt import Receipt
from thoth.domain.revision import RevisionChangeSet
from thoth.ports.ledger import LedgerPort
from thoth.ports.runtime import ClockPort, IdGeneratorPort


class CommitDisposition(StrEnum):
    FAST_FORWARD = "FAST_FORWARD"
    BRANCH = "BRANCH"


class CommitResult(DomainModel):
    disposition: CommitDisposition
    committed_revision_ids: tuple[RevisionId, ...]
    branch_revision_ids: tuple[RevisionId, ...]
    before_head_set_digest: Sha256
    after_head_set_digest: Sha256
    receipt: Receipt


class RevisionCommitService:
    def __init__(
        self,
        ledger: LedgerPort,
        clock: ClockPort,
        id_generator: IdGeneratorPort,
        *,
        policy_version: str,
    ) -> None:
        self._ledger = ledger
        self._clock = clock
        self._ids = id_generator
        self._policy_version = policy_version

    def commit(self, changeset: RevisionChangeSet) -> CommitResult:
        recorded_at = self._clock.now()
        impact_cause = domain_digest(
            "CHANGESET_IMPACT",
            "1.0.0",
            canonical_payload(
                {
                    "changeset_id": changeset.changeset_id,
                    "revision_digests": [
                        staged.revision.revision_digest for staged in changeset.staged_revisions
                    ],
                    "impact_plan": changeset.impact_plan,
                }
            ),
        )
        revision_ids = tuple(staged.revision.revision_id for staged in changeset.staged_revisions)
        with self._ledger.transaction() as transaction:
            for staged in changeset.staged_revisions:
                transaction.insert_snapshot(staged.snapshot)
                transaction.insert_revision(staged.revision)
            before_heads = dict(transaction.get_heads(changeset.project_id))
            before_digest = head_set_digest(before_heads)
            expected_matches = all(
                before_heads.get(key) == expected
                for key, expected in changeset.expected_heads.items()
            ) and (
                changeset.expected_head_set_digest is None
                or changeset.expected_head_set_digest == before_digest
            )
            after_heads = dict(before_heads)
            if expected_matches:
                for staged in changeset.staged_revisions:
                    aggregate_key = self._aggregate_key(
                        staged.revision.entity_type.value,
                        staged.revision.entity_id,
                    )
                    after_heads[aggregate_key] = staged.revision.revision_digest
                transaction.apply_impact_plan(
                    changeset.project_id,
                    changeset.impact_plan,
                    caused_by_revision=impact_cause,
                    updated_at=recorded_at.isoformat(),
                )
                for key, digest in after_heads.items():
                    if before_heads.get(key) != digest:
                        transaction.set_head(changeset.project_id, key, digest)
            after_digest = head_set_digest(after_heads)
            receipt = self._make_receipt(
                changeset.project_id,
                before_digest,
                after_digest,
                revision_ids,
                recorded_at,
                changeset.receipt_type,
                changeset.receipt_claim_scopes,
                changeset.actor,
            )
            transaction.insert_receipt(receipt)
        return CommitResult(
            disposition=(
                CommitDisposition.FAST_FORWARD if expected_matches else CommitDisposition.BRANCH
            ),
            committed_revision_ids=revision_ids if expected_matches else (),
            branch_revision_ids=() if expected_matches else revision_ids,
            before_head_set_digest=before_digest,
            after_head_set_digest=after_digest,
            receipt=receipt,
        )

    @staticmethod
    def _aggregate_key(entity_type: str, entity_id: str) -> str:
        return f"{entity_type}:{entity_id}"

    def _make_receipt(
        self,
        project_id: ProjectId,
        before_digest: Sha256,
        after_digest: Sha256,
        revision_ids: tuple[RevisionId, ...],
        recorded_at: AwareDatetime,
        receipt_type: ReceiptType,
        claim_scopes: tuple[ReceiptClaimScope, ...],
        actor: ActorRef,
    ) -> Receipt:
        receipt_id = self._ids.new("receipt")
        draft: dict[str, object] = {
            "receipt_id": receipt_id,
            "project_id": project_id,
            "receipt_type": receipt_type.value,
            "claim_scopes": [scope.value for scope in claim_scopes],
            "subject_refs": list(revision_ids),
            "before_head_set_digest": before_digest,
            "after_head_set_digest": after_digest,
            "parent_receipt_refs": [],
            "policy_version": self._policy_version,
            "schema_version": "1.0.0",
            "model_versions": [],
            "tool_versions": ["revision-service:0.1.0"],
            "integrity_state": IntegrityState.VALID.value,
            "provenance_state": ProvenanceState.COMPLETE.value,
            "signature_state": SignatureState.NOT_PRESENT.value,
            "timestamp_trust": TimestampTrust.LOCAL_ONLY.value,
            "recorded_at": recorded_at,
            "semantic_truth_certified": False,
            "actor_id": actor.actor_id,
            "session_id": actor.session_id,
            "role_assignment_ref": actor.role_assignment_ref,
        }
        digest = domain_digest("RECEIPT", "1.0.0", canonical_payload(draft))
        return Receipt.model_validate({**draft, "receipt_digest": digest})
