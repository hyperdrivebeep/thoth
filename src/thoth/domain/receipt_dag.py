from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import AwareDatetime, Field

from thoth.domain.base import DomainModel
from thoth.domain.ids import ProjectId, Sha256


class ReceiptNodeKind(StrEnum):
    CONNECTOR = "CONNECTOR"
    SANDBOX = "SANDBOX"
    EVIDENCE = "EVIDENCE"
    MODEL = "MODEL"
    ACTION = "ACTION"
    EXECUTION = "EXECUTION"
    OUTCOME = "OUTCOME"
    REVISION = "REVISION"
    MEMORY = "MEMORY"
    CLOSURE = "CLOSURE"
    EXPORT = "EXPORT"


class ReceiptTrustAxes(DomainModel):
    claim_scope: str
    integrity: str
    provenance: str
    authorization: Literal["SEPARATE"] = "SEPARATE"
    semantic_truth: Literal["NOT_CERTIFIED"] = "NOT_CERTIFIED"


class ReceiptDagNode(DomainModel):
    node_id: str
    project_id: ProjectId
    kind: ReceiptNodeKind
    subject_refs: tuple[str, ...]
    source_receipt_ref: str | None = None
    source_digest: Sha256
    axes: ReceiptTrustAxes
    node_digest: Sha256
    recorded_at: AwareDatetime


class ReceiptDagEdge(DomainModel):
    edge_id: str
    project_id: ProjectId
    parent_node_id: str
    child_node_id: str
    relation: str
    edge_digest: Sha256


class ReceiptDagManifest(DomainModel):
    manifest_id: str
    project_id: ProjectId
    nodes: tuple[ReceiptDagNode, ...]
    edges: tuple[ReceiptDagEdge, ...]
    roots: tuple[str, ...]
    axes: ReceiptTrustAxes
    manifest_digest: Sha256
    created_at: AwareDatetime


class ReceiptBundle(DomainModel):
    bundle_id: str
    project_id: ProjectId
    manifest_id: str
    node_ids: tuple[str, ...] = Field(min_length=1)
    ordering_rule: str
    bundle_digest: Sha256
    created_at: AwareDatetime


class ReceiptDagVerification(DomainModel):
    verification_id: str
    project_id: ProjectId
    subject_id: str
    integrity_state: str
    failed_refs: tuple[str, ...] = ()
    verification_digest: Sha256
    verified_at: AwareDatetime
    semantic_truth_certified: Literal[False] = False
