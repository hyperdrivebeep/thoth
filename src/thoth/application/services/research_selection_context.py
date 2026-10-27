"""Read scoped positional observations for candidate selection; retain the legacy path."""

import hashlib
from dataclasses import dataclass

from thoth.domain.artifact import StructuralDocument
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.context_observation import ContextObservation
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.retrieval_lane import CatalogContext
from thoth.ports.artifact_ledger import ArtifactLedgerPort


@dataclass(frozen=True)
class CandidateContext:
    ordered_refs: tuple[str, ...]
    protected_refs: frozenset[str]
    all_observed: bool
    structure_count: int
    basis_digest: str
    limitations: tuple[str, ...]

    def to_catalog(self) -> CatalogContext:
        return CatalogContext(
            self.ordered_refs, self.protected_refs, self.all_observed, self.structure_count
        )


def source_observation(
    document: StructuralDocument | None, anchor: EvidenceSpan
) -> ContextObservation | None:
    if document is None or document.context_observation is None:
        return None
    observation = document.context_observation
    if (
        observation.binding != "VERSION_BOUND"
        or observation.source_version_id != anchor.source_version_id
        or observation.project_id != anchor.project_id
        or observation.artifact_id != anchor.artifact_id
        or observation.byte_sha256 != document.artifact.byte_sha256
    ):
        raise ValueError("CONTEXT_OBSERVATION_VERSION_MISMATCH")
    return observation


def candidate_context(
    evidence: tuple[EvidenceSpan, ...], artifacts: ArtifactLedgerPort
) -> CandidateContext:
    groups: dict[tuple[str, str, str], list[EvidenceSpan]] = {}
    for span in evidence:
        groups.setdefault((span.project_id, span.artifact_id, span.source_version_id), []).append(
            span
        )
    ordered: list[str] = []
    protected: set[str] = set()
    bases: list[object] = []
    limitations: set[str] = set()
    definitions = 0
    all_observed = bool(groups)
    for key, spans in sorted(groups.items()):
        document = artifacts.read_structure(*key)
        observed = source_observation(document, spans[0])
        if observed is None or observed.status != "OBSERVED":
            all_observed = False
            limitations.add(
                "LEGACY_CONTEXT" if observed is None else "CONTEXT_OBSERVATION_UNAVAILABLE"
            )
            bases.append((key, None if observed is None else observed.model_dump(mode="json")))
            continue
        assert document is not None
        limitations.update(observed.warnings)
        source_nodes = {n.node_id: n for n in document.nodes}
        for span in spans:
            node = source_nodes.get(span.locator.structural_node_id or "")
            if (
                node is None
                or node.text != span.exact_text
                or hashlib.sha256(span.exact_text.encode()).hexdigest() != span.text_sha256
            ):
                raise ValueError("CONTEXT_SOURCE_MEMBER_INVALID")
        bases.append((key, observed.model_dump(mode="json")))
        ordinal = {n.node_id: n.ordinal for n in document.nodes}
        ordered.extend(
            s.span_id
            for s in sorted(spans, key=lambda s: ordinal[s.locator.structural_node_id or ""])
        )
        visible_nodes = {s.locator.structural_node_id: s.span_id for s in spans}
        regions = {r.region_id: r for r in observed.regions}
        leading = {ref for r in observed.regions for ref in r.leading_refs}
        for region in regions.values():
            if region.kind == "BLANK_INTERVAL":
                continue
            if set(region.node_refs) & visible_nodes.keys():
                definitions += 1
            if region.kind == "ATX_HEADING" or region.region_id in leading:
                protected.update(visible_nodes[n] for n in region.node_refs if n in visible_nodes)
    return CandidateContext(
        tuple(ordered),
        frozenset(protected),
        all_observed,
        definitions,
        domain_digest(
            "SELECTION_SOURCE_CONTEXT",
            "1.0.0",
            canonical_payload({"sources": evidence, "observations": bases}),
        ),
        tuple(sorted(limitations)),
    )
