"""Role-specific views retain necessary source text/authority without copying prior narratives."""

from thoth.domain.enums import ModelRole
from thoth.domain.evidence_bundle import ContextAssembly
from thoth.domain.model import ContextPack


def bundle_view(assembly: ContextAssembly) -> dict[str, object]:
    nodes = {node.node_id: node for bundle in assembly.bundles for node in bundle.structures}
    evidence_nodes = {s.locator.structural_node_id for s in assembly.evidence}
    projected: list[dict[str, object]] = []
    for node in nodes.values():
        # The actual text, digest and locator already live on the linked evidence span.
        fields: dict[str, object] = {
            "node_id": node.node_id,
            "kind": node.kind,
            "parent_id": node.parent_id,
        }
        if node.node_id not in evidence_nodes:
            fields["locator"] = node.locator.model_dump(mode="json", exclude_none=True)
        if node.relations:
            fields["relations"] = [r.model_dump(mode="json") for r in node.relations]
        if node.warnings:
            fields["warnings"] = node.warnings
        projected.append(fields)
    return {
        "bundles": [
            {
                "bundle_id": b.bundle_id,
                "artifact_id": b.artifact_id,
                "source_version_id": b.source_version_id,
                "anchor_span_refs": b.anchor_span_refs,
                "span_refs": b.span_refs,
                "basis_digest": b.basis_digest,
                "structure_refs": tuple(n.node_id for n in b.structures),
                "limitations": b.limitations,
            }
            for b in assembly.bundles
        ],
        "structure_nodes": projected,
        "internal_expansion": assembly.wave.model_dump(mode="json"),
    }


_COMMON = {
    "request_ref",
    "source_overview",
    "interpretation_open_items",
    "requirements",
    "requirement_set_ref",
    "connected_sources_only",
    "source_packet",
    "context_bundle",
    "gap_targets",
    "omitted_required_information",
    "task",
    "rules",
    "state",
    "source_context_digest",
    "source_context_version",
    "authorized_project_memory",
    "preferences",
    "preference_policy",
    "source_time_limitation",
    "entity_currentness",
}
_EXTRA: dict[ModelRole, set[str]] = {
    ModelRole.RESEARCH_PLANNER: {"profiles", "source_overview"},
    ModelRole.EVIDENCE_RERANKER: set(),
    ModelRole.SEMANTIC_REVIEWER: {"retrieval_limits"},
    ModelRole.REVIEW_ADJUDICATOR: {"candidate"},
}


def role_context(context: ContextPack, role: ModelRole) -> ContextPack:
    if role not in _EXTRA:
        return context
    keys = _COMMON | _EXTRA[role]
    data = {k: v for k, v in context.research_context.items() if k in keys}
    return context.model_copy(update={"research_context": data})
