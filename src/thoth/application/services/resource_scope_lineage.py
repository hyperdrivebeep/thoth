"""Seal actual read dependencies with each immutable research revision."""

from thoth.application.services.resource_scope_service import ResourceScopeService
from thoth.domain.receipt import Receipt
from thoth.domain.resource_scope import ResourceScopeError, current_resource_uses
from thoth.domain.revision import SemanticRevision
from thoth.ports.ledger import LedgerPort, LedgerProjectionPort, ReceiptProjectionPort


class ResearchResourceLineage(LedgerProjectionPort):
    def __init__(self, scopes: ResourceScopeService) -> None:
        self._scopes = scopes

    def stage_revision(self, revision: SemanticRevision) -> None:
        uses = current_resource_uses() or ()
        parents = (
            *revision.evidence_refs,
            *(f"revision:{digest}" for digest in revision.parent_revision_digests),
            *(
                use.resource_ref
                for use in uses
                if use.project_id == revision.project_id and use.capability == "READ"
            ),
        )
        self._scopes.record_revision_lineage(revision.project_id, revision.revision_digest, parents)


class ReceiptResourceLineage(ReceiptProjectionPort):
    def __init__(self, ledger: LedgerPort, scopes: ResourceScopeService) -> None:
        self._ledger = ledger
        self._scopes = scopes

    def stage_receipt(self, receipt: Receipt) -> None:
        parents: list[str] = []
        for ref in receipt.subject_refs:
            revision = self._ledger.read_revision_by_id(
                receipt.project_id, ref
            ) or self._ledger.read_revision_by_digest(receipt.project_id, ref)
            parents.append(ref if revision is None else f"revision:{revision.revision_digest}")
        parents.extend(receipt.evidence_refs)
        receipts = {r.receipt_id: r for r in self._ledger.read_receipts(receipt.project_id)}
        for identifier in receipt.parent_receipt_refs:
            parent = receipts.get(identifier)
            if parent is None:
                raise ResourceScopeError("RESOURCE_LINEAGE_UNKNOWN")
            parents.append(f"receipt:{parent.receipt_digest}")
        if receipt.previous_transition_digest is not None:
            parents.append(f"receipt:{receipt.previous_transition_digest}")
        parents.extend(
            use.resource_ref
            for use in current_resource_uses() or ()
            if use.project_id == receipt.project_id and use.capability == "READ"
        )
        self._scopes.record_receipt_lineage(
            receipt.project_id, receipt.receipt_digest, tuple(parents)
        )
