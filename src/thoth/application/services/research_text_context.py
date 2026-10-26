"""Positional text envelopes over admitted leaf spans; no source text rewriting."""

from dataclasses import dataclass

from thoth.application.services.research_selection_context import source_observation
from thoth.domain.artifact import StructuralDocument
from thoth.domain.context_observation import ContextRegion
from thoth.domain.evidence import EvidenceSpan


@dataclass(frozen=True)
class TextEnvelope:
    container: str
    regions: tuple[ContextRegion, ...]
    required: tuple[EvidenceSpan, ...]
    optional: tuple[EvidenceSpan, ...]
    limitations: tuple[str, ...]


def text_envelope(
    anchor: EvidenceSpan, document: StructuralDocument | None, source: list[EvidenceSpan]
) -> TextEnvelope | None:
    observed = source_observation(document, anchor)
    if observed is None or observed.status != "OBSERVED":
        return None
    regions = {r.region_id: r for r in observed.regions}
    enclosing = [r for r in observed.regions if anchor.locator.structural_node_id in r.node_refs]
    if not enclosing:
        return None  # A syntactic form outside the supported observation contract.
    region = min(enclosing, key=lambda r: (r.line_end - r.line_start, r.region_id))
    by_node = {s.locator.structural_node_id: s for s in source}
    context_ids = tuple(dict.fromkeys((*region.heading_refs, *region.leading_refs)))
    envelope = (region, *(regions[k] for k in context_ids))
    required: dict[str, EvidenceSpan] = {anchor.span_id: anchor}
    limitations = []
    for related in envelope[1:]:
        for node in related.node_refs:
            if node in by_node:
                required[by_node[node].span_id] = by_node[node]
            else:
                limitations.append("CONTEXT_MEMBER_NOT_ELIGIBLE")
    members = [by_node[k] for k in region.node_refs if k in by_node]
    index = next(i for i, s in enumerate(members) if s.span_id == anchor.span_id)
    optional = tuple(s for s in members[max(0, index - 1) : index + 2] if s.span_id not in required)
    return TextEnvelope(
        region.region_id, envelope, tuple(required.values()), optional, tuple(set(limitations))
    )
