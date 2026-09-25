"""Scoped, freshly sealed DAG views never relabel a filtered manifest with its old digest."""

from __future__ import annotations

from typing import cast

from thoth.application.services.receipt_dag_service import ReceiptDagService
from thoth.application.services.scoped_memory_controls import ScopedMemoryControls
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.control_record import ControlRecord
from thoth.domain.receipt import Receipt
from thoth.domain.receipt_dag import (
    ReceiptBundle,
    ReceiptDagEdge,
    ReceiptDagManifest,
    ReceiptDagNode,
    ReceiptDagVerification,
    ReceiptNodeKind,
)
from thoth.domain.resource_scope import ResourceScopeError
from thoth.ports.control_record import ControlRecordStorePort
from thoth.ports.ledger import LedgerPort
from thoth.ports.receipt_dag import ReceiptDagStorePort
from thoth.ports.resource_scope import ResourceAccessPort


class ScopedReceiptDagStore:
    def __init__(
        self,
        raw: ReceiptDagStorePort,
        ledger: LedgerPort,
        controls: ControlRecordStorePort,
        access: ResourceAccessPort,
    ) -> None:
        self._raw = raw
        self._ledger = ledger
        self._controls = controls
        self._access = access
        self._memories = ScopedMemoryControls(controls, access, ledger)

    def require_node(self, node: ReceiptDagNode) -> None:
        project = node.project_id
        if node.source_receipt_ref is not None:
            receipt = next(
                (
                    r
                    for r in self._ledger.read_receipts(project)
                    if r.receipt_id == node.source_receipt_ref
                ),
                None,
            )
            if receipt is None:
                raise ResourceScopeError("RESOURCE_LINEAGE_UNKNOWN")
            self._access.require_read(project, f"receipt:{receipt.receipt_digest}")
            return
        record = self._controls.read_digest(project, node.source_digest)
        if record is not None and record.namespace == "MEMORY":
            self._memories.read_digest(project, record.record_digest)
            return
        if record is not None and record.namespace == "CONNECTOR":
            run_id = record.payload.get("connector_run_id")
            run = (
                None
                if not isinstance(run_id, str)
                else self._controls.read(project, "CONNECTOR", run_id)
            )
            refs = None if run is None else run.payload.get("artifact_refs")
            self._require_refs(project, refs)
            return
        if record is not None and record.namespace == "SANDBOX":
            attempt = record.payload.get("attempt_id")
            runs = tuple(
                r
                for r in self._controls.list(project, "SANDBOX", "RUN")
                if r.payload.get("attempt_id") == attempt
            )
            if len(runs) != 1:
                raise ResourceScopeError("RESOURCE_LINEAGE_UNKNOWN")
            observation = runs[0].payload.get("observation_artifact_id")
            if isinstance(observation, str):
                self._access.require_read(project, observation)
                return
            self._require_refs(project, runs[0].payload.get("resource_parent_refs"))
            return
        if node.kind == ReceiptNodeKind.EVIDENCE:
            self._require_refs(project, node.subject_refs)
            return
        # Opaque historical model names and labels are not source lineage.
        raise ResourceScopeError("RESOURCE_LINEAGE_UNKNOWN")

    def _require_refs(self, project: str, value: object) -> None:
        if not isinstance(value, tuple | list) or not value:
            raise ResourceScopeError("RESOURCE_LINEAGE_UNKNOWN")
        refs = cast(tuple[object, ...] | list[object], value)
        if any(not isinstance(ref, str) for ref in refs):
            raise ResourceScopeError("RESOURCE_LINEAGE_UNKNOWN")
        self._access.require_reads(project, tuple(str(ref) for ref in refs))

    def may_read(self, node: ReceiptDagNode) -> bool:
        try:
            self.require_node(node)
        except ResourceScopeError as exc:
            if exc.code in {
                "RESOURCE_ACCESS_DENIED",
                "RESOURCE_SCOPE_UNKNOWN",
                "RESOURCE_REFERENCE_UNRESOLVED",
                "RESOURCE_LINEAGE_UNKNOWN",
            }:
                return False
            raise
        return True

    def view(self, manifest: ReceiptDagManifest) -> ReceiptDagManifest:
        nodes = tuple(n for n in manifest.nodes if self.may_read(n))
        if len(nodes) == len(manifest.nodes):
            return manifest
        identifiers = {n.node_id for n in nodes}
        edges = tuple(
            e
            for e in manifest.edges
            if e.parent_node_id in identifiers and e.child_node_id in identifiers
        )
        children = {e.child_node_id for e in edges}
        draft = manifest.model_dump(mode="python", exclude={"manifest_digest"})
        draft.update(
            {
                "manifest_id": f"{manifest.manifest_id}:scoped",
                "nodes": nodes,
                "edges": edges,
                "roots": tuple(n.node_id for n in nodes if n.node_id not in children),
                "axes": manifest.axes.model_copy(update={"provenance": "PARTIAL"}),
            }
        )
        return ReceiptDagManifest.model_validate(
            {
                **draft,
                "manifest_digest": domain_digest(
                    "RECEIPT_DAG_MANIFEST", "1.0.0", canonical_payload(draft)
                ),
            }
        )

    def list_nodes(self, project_id: str) -> tuple[ReceiptDagNode, ...]:
        return tuple(n for n in self._raw.list_nodes(project_id) if self.may_read(n))

    def list_edges(self, project_id: str) -> tuple[ReceiptDagEdge, ...]:
        ids = {n.node_id for n in self.list_nodes(project_id)}
        return tuple(
            e
            for e in self._raw.list_edges(project_id)
            if e.parent_node_id in ids and e.child_node_id in ids
        )

    def put_node(self, value: ReceiptDagNode) -> None:
        self.require_node(value)
        self._raw.put_node(value)

    def _require_ids(self, project_id: str, ids: tuple[str, ...]) -> None:
        nodes = {n.node_id: n for n in self._raw.list_nodes(project_id)}
        for identifier in ids:
            node = nodes.get(identifier)
            if node is None:
                raise ResourceScopeError("RESOURCE_LINEAGE_UNKNOWN")
            self.require_node(node)

    def put_edge(self, value: ReceiptDagEdge) -> None:
        self._require_ids(value.project_id, (value.parent_node_id, value.child_node_id))
        self._raw.put_edge(value)

    def put_manifest(self, value: ReceiptDagManifest) -> None:
        self._require_ids(value.project_id, tuple(n.node_id for n in value.nodes))
        self._raw.put_manifest(value)

    def read_manifest(self, project_id: str) -> ReceiptDagManifest | None:
        value = self._raw.read_manifest(project_id)
        return None if value is None else self.view(value)

    def put_bundle(self, value: ReceiptBundle) -> None:
        self._require_ids(value.project_id, value.node_ids)
        self._raw.put_bundle(value)

    def read_bundle(self, project_id: str, bundle_id: str) -> ReceiptBundle | None:
        value = self._raw.read_bundle(project_id, bundle_id)
        if value is not None:
            self._require_ids(project_id, value.node_ids)
        return value

    def require_subject(self, project: str, identifier: str) -> None:
        bundle = self._raw.read_bundle(project, identifier)
        if bundle is not None:
            self._require_ids(project, bundle.node_ids)
            return
        receipt = next(
            (r for r in self._ledger.read_receipts(project) if r.receipt_id == identifier), None
        )
        if receipt is None:
            raise ResourceScopeError("RESOURCE_LINEAGE_UNKNOWN")
        self._access.require_read(project, f"receipt:{receipt.receipt_digest}")

    def put_verification(self, value: ReceiptDagVerification) -> None:
        self.require_subject(value.project_id, value.subject_id)
        self._raw.put_verification(value)

    def list_verifications(
        self, project_id: str, subject_id: str | None = None
    ) -> tuple[ReceiptDagVerification, ...]:
        values: list[ReceiptDagVerification] = []
        for value in self._raw.list_verifications(project_id, subject_id):
            try:
                self.require_subject(project_id, value.subject_id)
            except ResourceScopeError as exc:
                if exc.code in {
                    "RESOURCE_ACCESS_DENIED",
                    "RESOURCE_SCOPE_UNKNOWN",
                    "RESOURCE_REFERENCE_UNRESOLVED",
                    "RESOURCE_LINEAGE_UNKNOWN",
                }:
                    continue
                raise
            values.append(value)
        return tuple(values)


class ScopedReceiptDagService(ReceiptDagService):
    def __init__(self, raw: ReceiptDagService, visible: ScopedReceiptDagStore) -> None:
        self._delegate = raw
        self._visible = visible

    def sync(self, project_id: str) -> ReceiptDagManifest:
        return self._visible.view(self._delegate.sync(project_id))

    def record_cycle(
        self,
        project_id: str,
        values: dict[ReceiptNodeKind, tuple[str, ...]],
        receipt: Receipt | None = None,
    ) -> ReceiptDagManifest:
        return self._visible.view(self._delegate.record_cycle(project_id, values, receipt))


class ScopedReceiptControls:
    def __init__(self, raw: ControlRecordStorePort, dag: ScopedReceiptDagStore) -> None:
        self._raw = raw
        self._dag = dag

    def _require(self, value: ControlRecord) -> None:
        if value.namespace != "RECEIPT":
            raise ResourceScopeError("RESOURCE_LINEAGE_UNKNOWN")
        subjects = tuple(
            value.payload[key]
            for key in (
                "receipt_id",
                "subject_id",
                "bundle_id",
                "original_receipt_id",
                "corrected_receipt_id",
            )
            if key in value.payload
        )
        if not subjects or any(not isinstance(subject, str) for subject in subjects):
            raise ResourceScopeError("RESOURCE_LINEAGE_UNKNOWN")
        for subject in subjects:
            self._dag.require_subject(value.project_id, str(subject))

    def append(self, value: ControlRecord) -> None:
        self._require(value)
        self._raw.append(value)

    def read(self, project_id: str, namespace: str, record_id: str) -> ControlRecord | None:
        item = self._raw.read(project_id, namespace, record_id)
        if item is not None:
            self._require(item)
        return item

    def read_digest(self, project_id: str, digest: str) -> ControlRecord | None:
        item = self._raw.read_digest(project_id, digest)
        if item is not None:
            self._require(item)
        return item

    def list(
        self,
        project_id: str,
        namespace: str,
        record_type: str | None = None,
        *,
        latest_only: bool = True,
    ) -> tuple[ControlRecord, ...]:
        result: list[ControlRecord] = []
        for item in self._raw.list(project_id, namespace, record_type, latest_only=latest_only):
            try:
                self._require(item)
            except ResourceScopeError as exc:
                if exc.code in {
                    "RESOURCE_ACCESS_DENIED",
                    "RESOURCE_SCOPE_UNKNOWN",
                    "RESOURCE_REFERENCE_UNRESOLVED",
                    "RESOURCE_LINEAGE_UNKNOWN",
                }:
                    continue
                raise
            result.append(item)
        return tuple(result)
