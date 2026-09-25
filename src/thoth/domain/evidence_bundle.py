"""Derived context views over existing versioned source/evidence, not new source truth."""

from typing import Literal

from thoth.domain.artifact import SourceLocator, StructuralRelation
from thoth.domain.base import DomainModel
from thoth.domain.evidence import EvidenceSpan


class ContextStructureRef(DomainModel):
    node_id: str
    kind: str
    parent_id: str | None
    locator: SourceLocator
    text_digest: str | None
    relations: tuple[StructuralRelation, ...] = ()
    warnings: tuple[str, ...] = ()


class EvidenceContextBundle(DomainModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    bundle_id: str
    project_id: str
    artifact_id: str
    source_version_id: str
    container_id: str
    anchor_span_refs: tuple[str, ...]
    span_refs: tuple[str, ...]
    span_digests: dict[str, str]
    structures: tuple[ContextStructureRef, ...]
    basis_digest: str
    limitations: tuple[str, ...] = ()


class InternalExpansionWave(DomainModel):
    mode: Literal["CONNECTED_INTERNAL"] = "CONNECTED_INTERNAL"
    cache_hit: bool = False
    basis_digest: str
    anchor_count: int
    selected_count: int
    new_information_count: int
    omitted_anchor_refs: tuple[str, ...]
    missing_structure_refs: tuple[str, ...]
    selected_bytes: int
    character_budget: int
    node_budget: int
    termination: Literal[
        "COMPLETE_LOCAL_GROUPS", "NO_NEW_INFORMATION", "POLICY_LIMIT", "STRUCTURE_UNAVAILABLE"
    ]


class ContextAssembly(DomainModel):
    evidence: tuple[EvidenceSpan, ...]
    bundles: tuple[EvidenceContextBundle, ...]
    wave: InternalExpansionWave
