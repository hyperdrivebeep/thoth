from __future__ import annotations

from itertools import pairwise

from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.receipt import Receipt, calculate_receipt_digest
from thoth.domain.receipt_dag import (
    ReceiptDagEdge,
    ReceiptDagManifest,
    ReceiptDagNode,
    ReceiptNodeKind,
    ReceiptTrustAxes,
)
from thoth.ports.control_record import ControlRecordStorePort
from thoth.ports.ledger import LedgerPort
from thoth.ports.receipt_dag import ReceiptDagStorePort
from thoth.ports.runtime import ClockPort


class ReceiptDagService:
    def __init__(
        self,
        *,
        store: ReceiptDagStorePort,
        ledger: LedgerPort,
        controls: ControlRecordStorePort,
        clock: ClockPort,
    ) -> None:
        self._store = store
        self._ledger = ledger
        self._controls = controls
        self._clock = clock

    def record_cycle(
        self,
        project_id: str,
        values: dict[ReceiptNodeKind, tuple[str, ...]],
        receipt: Receipt | None = None,
    ) -> ReceiptDagManifest:
        for kind, refs in values.items():
            if refs:
                source: dict[str, object] = {"refs": refs}
                if receipt is not None:
                    source["cycle_receipt"] = receipt.receipt_digest
                self._node(
                    project_id,
                    kind,
                    refs,
                    None if receipt is None else receipt.receipt_id,
                    domain_digest(
                        kind.value,
                        "1.0.0",
                        canonical_payload(source),
                    ),
                )
        return self.sync(project_id)

    def sync(self, project_id: str) -> ReceiptDagManifest:
        for receipt in self._ledger.read_receipts(project_id):
            self._node(
                project_id,
                ReceiptNodeKind.REVISION,
                receipt.subject_refs or (receipt.receipt_id,),
                receipt.receipt_id,
                receipt.receipt_digest,
                axes=ReceiptTrustAxes(
                    claim_scope=",".join(scope.value for scope in receipt.claim_scopes),
                    integrity=(
                        "VALID"
                        if calculate_receipt_digest(receipt) == receipt.receipt_digest
                        else "INVALID"
                    ),
                    provenance=receipt.provenance_state.value,
                ),
            )
        for namespace, kind in (
            ("CONNECTOR", ReceiptNodeKind.CONNECTOR),
            ("SANDBOX", ReceiptNodeKind.SANDBOX),
            ("MEMORY", ReceiptNodeKind.MEMORY),
            ("CLOSURE", ReceiptNodeKind.CLOSURE),
            ("EXPORT", ReceiptNodeKind.EXPORT),
        ):
            for record in self._controls.list(project_id, namespace, None, latest_only=False):
                if record.record_type == "RECEIPT" or namespace in {"MEMORY", "CLOSURE", "EXPORT"}:
                    self._node(project_id, kind, (record.record_id,), None, record.record_digest)
        nodes = tuple(
            sorted(
                self._store.list_nodes(project_id),
                key=lambda item: (item.recorded_at, item.node_id),
            )
        )
        existing_edge_ids = {edge.edge_id for edge in self._store.list_edges(project_id)}
        for parent, child in pairwise(nodes):
            edge_id = self._edge_id(parent.node_id, child.node_id, "PRECEDES")
            if edge_id not in existing_edge_ids:
                self._edge(project_id, parent.node_id, child.node_id, "PRECEDES")
        edges = tuple(sorted(self._store.list_edges(project_id), key=lambda item: item.edge_id))
        node_ids = {node.node_id for node in nodes}
        integrity = (
            "VALID"
            if all(node.axes.integrity == "VALID" for node in nodes)
            and all(
                edge.parent_node_id in node_ids and edge.child_node_id in node_ids for edge in edges
            )
            else "INVALID"
        )
        child_ids = {edge.child_node_id for edge in edges}
        roots = tuple(node.node_id for node in nodes if node.node_id not in child_ids)
        axes = ReceiptTrustAxes(
            claim_scope="MIXED",
            integrity=integrity,
            provenance=(
                "COMPLETE"
                if nodes and all(node.axes.provenance == "COMPLETE" for node in nodes)
                else "PARTIAL"
            ),
        )
        draft = {
            "manifest_id": f"receipt-dag:{project_id}",
            "project_id": project_id,
            "nodes": nodes,
            "edges": edges,
            "roots": roots,
            "axes": axes,
            "created_at": self._clock.now(),
        }
        manifest = ReceiptDagManifest.model_validate(
            {
                **draft,
                "manifest_digest": domain_digest(
                    "RECEIPT_DAG_MANIFEST", "1.0.0", canonical_payload(draft)
                ),
            }
        )
        self._store.put_manifest(manifest)
        return manifest

    def _node(
        self,
        project_id: str,
        kind: ReceiptNodeKind,
        refs: tuple[str, ...],
        source_ref: str | None,
        source_digest: str,
        *,
        axes: ReceiptTrustAxes | None = None,
    ) -> None:
        identity: dict[str, object] = {"refs": refs}
        if source_ref is not None:
            identity["source_receipt_ref"] = source_ref
        identifier = domain_digest("NODE_ID", "1.0.0", canonical_payload(identity))[:24]
        node_id = f"receipt-node:{kind.value}:{identifier}"
        axes = axes or ReceiptTrustAxes(
            claim_scope=kind.value, integrity="VALID", provenance="COMPLETE"
        )
        draft = {
            "node_id": node_id,
            "project_id": project_id,
            "kind": kind,
            "subject_refs": refs,
            "source_receipt_ref": source_ref,
            "source_digest": source_digest,
            "axes": axes,
            "recorded_at": self._clock.now(),
        }
        self._store.put_node(
            ReceiptDagNode.model_validate(
                {
                    **draft,
                    "node_digest": domain_digest(
                        "RECEIPT_DAG_NODE", "1.0.0", canonical_payload(draft)
                    ),
                }
            )
        )

    def _edge(self, project_id: str, parent: str, child: str, relation: str) -> None:
        edge_id = self._edge_id(parent, child, relation)
        draft = {
            "edge_id": edge_id,
            "project_id": project_id,
            "parent_node_id": parent,
            "child_node_id": child,
            "relation": relation,
        }
        self._store.put_edge(
            ReceiptDagEdge.model_validate(
                {
                    **draft,
                    "edge_digest": domain_digest(
                        "RECEIPT_DAG_EDGE", "1.0.0", canonical_payload(draft)
                    ),
                }
            )
        )

    @staticmethod
    def _edge_id(parent: str, child: str, relation: str) -> str:
        identifier = domain_digest(
            "EDGE_ID",
            "1.0.0",
            canonical_payload({"parent": parent, "child": child, "relation": relation}),
        )[:24]
        return f"receipt-edge:{identifier}"
