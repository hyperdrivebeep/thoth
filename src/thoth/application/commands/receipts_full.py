from __future__ import annotations

from typing import cast

from pydantic import Field, JsonValue

from thoth.application.services.control_record_service import ControlRecordService
from thoth.application.services.receipt_dag_service import ReceiptDagService
from thoth.domain.auth import current_authenticated_actor
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
from thoth.domain.receipt import Receipt, calculate_receipt_digest
from thoth.domain.receipt_dag import ReceiptBundle, ReceiptDagVerification
from thoth.ports.control_record import ControlRecordStorePort
from thoth.ports.ledger import LedgerPort
from thoth.ports.receipt_dag import ReceiptDagStorePort
from thoth.ports.runtime import AtomicUnitOfWorkPort, ClockPort, IdGeneratorPort
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode


class ProjectInput(DomainModel):
    project_id: str = Field(min_length=1, max_length=160)


class ReceiptReadInput(ProjectInput):
    receipt_id: str = Field(min_length=1, max_length=160)


class StreamReadInput(ProjectInput):
    receipt_type: str | None = Field(default=None, max_length=80)


class BundleReadInput(ProjectInput):
    bundle_id: str = Field(min_length=1, max_length=160)


class SealInput(ProjectInput):
    receipt_type: ReceiptType
    claim_scopes: tuple[ReceiptClaimScope, ...]
    subject_refs: tuple[str, ...]
    before_head_set_digest: str = Field(min_length=64, max_length=64)
    after_head_set_digest: str = Field(min_length=64, max_length=64)
    actor_or_agent_ref: str = Field(min_length=1, max_length=160)
    evidence_refs: tuple[str, ...]
    policy_version: str = Field(min_length=1, max_length=160)
    schema_version: str = Field(default="1.0.0", max_length=40)
    model_versions: tuple[str, ...] = ()
    tool_versions: tuple[str, ...] = ()
    predecessor_receipt_ref: str | None = Field(default=None, max_length=160)
    parent_receipt_refs: tuple[str, ...] = ()


class BundleCreateInput(ProjectInput):
    receipt_ids: tuple[str, ...]
    ordering_rule: str = Field(default="RECORDED_AT_THEN_ID", max_length=160)


class CorrectionCreateInput(ReceiptReadInput):
    correction_reason: str = Field(min_length=1, max_length=2_000)
    corrected_subject_refs: tuple[str, ...]


class ReceiptHandlers:
    def __init__(
        self,
        *,
        ledger: LedgerPort,
        records: ControlRecordStorePort,
        controls: ControlRecordService,
        clock: ClockPort,
        ids: IdGeneratorPort,
        dag: ReceiptDagService,
        dag_store: ReceiptDagStorePort,
        unit_of_work: AtomicUnitOfWorkPort,
    ) -> None:
        self._ledger = ledger
        self._records = records
        self._controls = controls
        self._clock = clock
        self._ids = ids
        self._dag = dag
        self._dag_store = dag_store
        self._unit_of_work = unit_of_work

    async def list(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ProjectInput.model_validate(value)
        return {
            "receipts": [
                item.model_dump(mode="json")
                for item in self._ledger.read_receipts(request.project_id)
            ]
        }

    async def read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ReceiptReadInput.model_validate(value)
        return {"receipt": self._receipt(request).model_dump(mode="json")}

    async def stream_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = StreamReadInput.model_validate(value)
        items = tuple(
            item
            for item in self._ledger.read_receipts(request.project_id)
            if request.receipt_type is None or item.receipt_type.value == request.receipt_type
        )
        return {
            "stream": [item.model_dump(mode="json") for item in items],
            "predecessor_chain": [item.previous_transition_digest for item in items],
        }

    async def lineage_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ReceiptReadInput.model_validate(value)
        root = self._receipt(request)
        by_id = {item.receipt_id: item for item in self._ledger.read_receipts(request.project_id)}
        parent_nodes: list[Receipt] = []
        frontier = list(root.parent_receipt_refs)
        while frontier:
            item = by_id.get(frontier.pop(0))
            if item is None or item in parent_nodes:
                continue
            parent_nodes.append(item)
            frontier.extend(item.parent_receipt_refs)
        return {
            "aggregate_predecessor_chain": root.previous_transition_digest,
            "parent_receipt_dag": [item.model_dump(mode="json") for item in parent_nodes],
        }

    async def bundle_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = BundleReadInput.model_validate(value)
        item = self._dag_store.read_bundle(request.project_id, request.bundle_id)
        if item is None:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "bundle not found")
        return {"bundle": item.model_dump(mode="json")}

    async def verification_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ReceiptReadInput.model_validate(value)
        items = self._dag_store.list_verifications(request.project_id, request.receipt_id)
        return {"verifications": [item.model_dump(mode="json") for item in items]}

    async def audit_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ProjectInput.model_validate(value)
        dag = self._dag.sync(request.project_id)
        return {
            "receipts": [
                item.model_dump(mode="json")
                for item in self._ledger.read_receipts(request.project_id)
            ],
            "control_records": [
                item.model_dump(mode="json")
                for item in self._records.list(
                    request.project_id, "RECEIPT", None, latest_only=False
                )
            ],
            "dag": dag.model_dump(mode="json"),
        }

    async def seal(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        with self._unit_of_work.transaction():
            request = SealInput.model_validate(value)
            authenticated = current_authenticated_actor()
            if authenticated is not None and (
                authenticated.project_id != request.project_id
                or authenticated.actor_id != request.actor_or_agent_ref
            ):
                raise RpcApplicationError(
                    RpcErrorCode.AUTHORIZATION_DENIED,
                    "authenticated actor does not match receipt sealer",
                )
            actor_id = (
                request.actor_or_agent_ref if authenticated is None else authenticated.actor_id
            )
            previous = (
                None
                if request.predecessor_receipt_ref is None
                else self._receipt(
                    ReceiptReadInput(
                        project_id=request.project_id,
                        receipt_id=request.predecessor_receipt_ref,
                    )
                )
            )
            for parent in request.parent_receipt_refs:
                self._receipt(ReceiptReadInput(project_id=request.project_id, receipt_id=parent))
            receipt_id = self._ids.new("receipt")
            draft: dict[str, object] = {
                "receipt_id": receipt_id,
                "project_id": request.project_id,
                "receipt_type": request.receipt_type,
                "claim_scopes": request.claim_scopes,
                "subject_refs": request.subject_refs,
                "evidence_refs": request.evidence_refs,
                "before_head_set_digest": request.before_head_set_digest,
                "after_head_set_digest": request.after_head_set_digest,
                "previous_transition_digest": (
                    None if previous is None else previous.receipt_digest
                ),
                "parent_receipt_refs": request.parent_receipt_refs,
                "policy_version": request.policy_version,
                "schema_version": request.schema_version,
                "model_versions": request.model_versions,
                "tool_versions": request.tool_versions,
                "integrity_state": IntegrityState.VALID,
                # Public assertions are durable, but authentication proves the actor only.
                # Producer-bound transitions retain their separate trusted recording path.
                "provenance_state": ProvenanceState.PARTIAL,
                "signature_state": SignatureState.NOT_PRESENT,
                "timestamp_trust": TimestampTrust.LOCAL_ONLY,
                "recorded_at": self._clock.now(),
                "actor_id": actor_id,
                "session_id": None if authenticated is None else authenticated.session_id,
                "role_assignment_ref": (
                    None if authenticated is None else authenticated.role_assignment_id
                ),
                "semantic_truth_certified": False,
            }
            temporary = Receipt.model_validate({**draft, "receipt_digest": "0" * 64})
            receipt = temporary.model_copy(
                update={"receipt_digest": calculate_receipt_digest(temporary)}
            )
            with self._ledger.transaction() as transaction:
                transaction.insert_receipt(receipt)
            return {
                "receipt": receipt.model_dump(mode="json"),
                "seal_state": "SEALED",
                "semantic_truth_certified": False,
                "actor_or_agent_ref": actor_id,
                "evidence_refs": list(request.evidence_refs),
            }

    async def verify(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ReceiptReadInput.model_validate(value)
        receipt = self._receipt(request)
        calculated = calculate_receipt_digest(receipt)
        integrity = "VALID" if calculated == receipt.receipt_digest else "INVALID"
        failed: list[str] = []
        if integrity == "INVALID":
            failed.extend(scope.value for scope in receipt.claim_scopes)
        verification_draft = {
            "verification_id": self._ids.new("receipt-verification"),
            "project_id": request.project_id,
            "subject_id": receipt.receipt_id,
            "integrity_state": integrity,
            "failed_refs": tuple(failed),
            "verified_at": self._clock.now(),
            "semantic_truth_certified": False,
        }
        verification = ReceiptDagVerification.model_validate(
            {
                **verification_draft,
                "verification_digest": domain_digest(
                    "RECEIPT_VERIFICATION",
                    "1.0.0",
                    canonical_payload(verification_draft),
                ),
            }
        )
        self._dag_store.put_verification(verification)
        return {
            "verification": cast(
                JsonValue,
                {
                    **verification.model_dump(mode="json"),
                    "record_id": verification.verification_id,
                    "state": "VERIFIED" if integrity == "VALID" else "FAILED",
                    "payload": {
                        "receipt_id": receipt.receipt_id,
                        "integrity_state": integrity,
                        "provenance_state": receipt.provenance_state.value,
                        "signature_state": receipt.signature_state.value,
                        "timestamp_trust": receipt.timestamp_trust.value,
                        "failed_claim_scopes": tuple(failed),
                        "semantic_truth_certified": False,
                    },
                },
            )
        }

    async def bundle_create(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = BundleCreateInput.model_validate(value)
        for receipt_id in request.receipt_ids:
            self._receipt(
                ReceiptReadInput(
                    project_id=request.project_id,
                    receipt_id=receipt_id,
                )
            )
        manifest = self._dag.sync(request.project_id)
        selected = tuple(
            node.node_id
            for node in manifest.nodes
            if not request.receipt_ids or node.source_receipt_ref in request.receipt_ids
        )
        if request.receipt_ids and not selected:
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED,
                "requested receipts are absent from the receipt DAG",
            )
        if not request.receipt_ids:
            selected = tuple(node.node_id for node in manifest.nodes)
        draft = {
            "bundle_id": self._ids.new("receipt-bundle"),
            "project_id": request.project_id,
            "manifest_id": manifest.manifest_id,
            "node_ids": selected,
            "ordering_rule": request.ordering_rule,
            "created_at": self._clock.now(),
        }
        bundle = ReceiptBundle.model_validate(
            {
                **draft,
                "bundle_digest": domain_digest("RECEIPT_BUNDLE", "1.0.0", canonical_payload(draft)),
            }
        )
        self._dag_store.put_bundle(bundle)
        return {
            "bundle": {
                **bundle.model_dump(mode="json"),
                "record_id": bundle.bundle_id,
                "state": "CREATED",
                "payload": bundle.model_dump(mode="json"),
            }
        }

    async def bundle_verify(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = BundleReadInput.model_validate(value)
        bundle = self._dag_store.read_bundle(request.project_id, request.bundle_id)
        if bundle is None:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "bundle not found")
        manifest = self._dag.sync(request.project_id)
        node_ids = {node.node_id for node in manifest.nodes}
        calculated = domain_digest(
            "RECEIPT_BUNDLE",
            "1.0.0",
            canonical_payload(
                {
                    "bundle_id": bundle.bundle_id,
                    "project_id": bundle.project_id,
                    "manifest_id": bundle.manifest_id,
                    "node_ids": bundle.node_ids,
                    "ordering_rule": bundle.ordering_rule,
                    "created_at": bundle.created_at,
                }
            ),
        )
        valid = calculated == bundle.bundle_digest and set(bundle.node_ids) <= node_ids
        verification_draft = {
            "verification_id": self._ids.new("bundle-verification"),
            "project_id": request.project_id,
            "subject_id": bundle.bundle_id,
            "integrity_state": "VALID" if valid else "INVALID",
            "failed_refs": (() if valid else bundle.node_ids),
            "verified_at": self._clock.now(),
            "semantic_truth_certified": False,
        }
        verification = ReceiptDagVerification.model_validate(
            {
                **verification_draft,
                "verification_digest": domain_digest(
                    "RECEIPT_BUNDLE_VERIFICATION",
                    "1.0.0",
                    canonical_payload(verification_draft),
                ),
            }
        )
        self._dag_store.put_verification(verification)
        return {
            "bundle_id": bundle.bundle_id,
            "integrity_state": "VALID" if valid else "INVALID",
            "semantic_truth_certified": False,
            "verification": verification.model_dump(mode="json"),
        }

    async def correction_create(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        with self._unit_of_work.transaction():
            request = CorrectionCreateInput.model_validate(value)
            original = self._receipt(request)
            authenticated = current_authenticated_actor()
            receipt_id = self._ids.new("receipt")
            draft: dict[str, object] = {
                **original.model_dump(mode="python"),
                "receipt_id": receipt_id,
                "subject_refs": request.corrected_subject_refs,
                "provenance_state": ProvenanceState.PARTIAL,
                "previous_transition_digest": original.receipt_digest,
                "parent_receipt_refs": (original.receipt_id,),
                "recorded_at": self._clock.now(),
                "actor_id": (
                    original.actor_id if authenticated is None else authenticated.actor_id
                ),
                "session_id": (None if authenticated is None else authenticated.session_id),
                "role_assignment_ref": (
                    None if authenticated is None else authenticated.role_assignment_id
                ),
            }
            draft.pop("receipt_digest", None)
            temporary = Receipt.model_validate({**draft, "receipt_digest": "0" * 64})
            corrected = temporary.model_copy(
                update={"receipt_digest": calculate_receipt_digest(temporary)}
            )
            with self._ledger.transaction() as transaction:
                transaction.insert_receipt(corrected)
            correction = self._controls.create(
                project_id=request.project_id,
                namespace="RECEIPT",
                record_type="CORRECTION",
                state="CREATED",
                payload={
                    "original_receipt_id": original.receipt_id,
                    "corrected_receipt_id": corrected.receipt_id,
                    "original_actor_id": original.actor_id,
                    "correcting_actor_id": corrected.actor_id,
                    "correction_reason": request.correction_reason,
                    "original_mutated": False,
                },
            )
            return {
                "receipt": corrected.model_dump(mode="json"),
                "correction": correction.model_dump(mode="json"),
                "original_mutated": False,
            }

    def _receipt(self, request: ReceiptReadInput) -> Receipt:
        item = next(
            (
                receipt
                for receipt in self._ledger.read_receipts(request.project_id)
                if receipt.receipt_id == request.receipt_id
            ),
            None,
        )
        if item is None:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "receipt not found")
        return item
