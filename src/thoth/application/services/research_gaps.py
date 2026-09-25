"""Verify explicit structure/identity; semantic resolution remains proposed and adjudicated."""

from thoth.domain.artifact import StructuralRelation
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.evidence_bundle import EvidenceContextBundle
from thoth.domain.evidence_gap import GapTarget, GapValidation
from thoth.domain.evidence_requirements import (
    EvidenceRanking,
    RequirementSetRevision,
    ReviewAdjudication,
    ReviewProposal,
)
from thoth.domain.research_request import RevisionRef


def gap_targets(
    ranking: EvidenceRanking,
    requirement_ref: RevisionRef,
    bundles: tuple[EvidenceContextBundle, ...] = (),
    evidence: tuple[EvidenceSpan, ...] = (),
) -> tuple[GapTarget, ...]:
    result: list[GapTarget] = []
    for gap in ranking.gap_proposals:
        result.append(
            GapTarget(
                gap_id=gap.gap_id,
                omission_text=gap.omission_text,
                requirement_id=gap.requirement_id,
                anchor_span_refs=gap.anchor_span_refs,
                required_relation_kinds=gap.required_relation_kinds,
                origin="RERANKER",
            )
        )
    described = {gap.omission_text for gap in result}
    for text in dict.fromkeys(ranking.omitted_required_information):
        if text not in described:
            digest = domain_digest(
                "OMISSION_TARGET",
                "1.0.0",
                canonical_payload({"requirements": requirement_ref, "text": text}),
            )
            result.append(
                GapTarget(
                    gap_id=f"gap:{digest}",
                    omission_text=text,
                    requirement_id=None,
                    origin="LEGACY_RERANKER",
                )
            )
    seen = {g.gap_id for g in result}
    for bundle in bundles:
        for node in bundle.structures:
            for relation in node.relations:
                if relation.basis != "INFERRED":
                    continue
                digest = domain_digest(
                    "UNREVIEWED_STRUCTURE_RELATION",
                    "1.0.0",
                    canonical_payload(
                        {
                            "requirements": requirement_ref,
                            "source_version": bundle.source_version_id,
                            "node": node.node_id,
                            "relation": relation,
                        }
                    ),
                )
                identifier = f"gap:{digest}"
                if identifier in seen:
                    continue
                seen.add(identifier)
                result.append(
                    GapTarget(
                        gap_id=identifier,
                        omission_text=f"Unreviewed {relation.kind} association "
                        f"for node {node.node_id}",
                        requirement_id=None,
                        anchor_span_refs=tuple(
                            s.span_id
                            for s in evidence
                            if s.locator.structural_node_id == node.node_id
                        ),
                        required_relation_kinds=(relation.kind,),
                        origin="STRUCTURE_OBSERVATION",
                    )
                )
    return tuple(result)


def validate_gaps(
    targets: tuple[GapTarget, ...],
    requirements: RequirementSetRevision,
    ref: RevisionRef,
    candidate: ReviewProposal,
    decision: ReviewAdjudication,
    evidence: tuple[EvidenceSpan, ...],
    bundles: tuple[EvidenceContextBundle, ...],
    run: str,
) -> tuple[GapValidation, ...]:
    proposed = {g.gap_id: g for g in candidate.gap_proposals}
    reviewed = {g.gap_id: g for g in decision.gap_decisions}
    target_map = {g.gap_id: g for g in targets}
    duplicate = (
        len(target_map) != len(targets)
        or len(proposed) != len(candidate.gap_proposals)
        or len(reviewed) != len(decision.gap_decisions)
    )
    for gap in candidate.gap_proposals:
        target_map.setdefault(
            gap.gap_id,
            GapTarget(
                gap_id=gap.gap_id,
                omission_text=gap.omission_text,
                requirement_id=gap.requirement_id,
                anchor_span_refs=gap.anchor_span_refs,
                required_relation_kinds=gap.required_relation_kinds,
                origin="SEMANTIC_REVIEW",
            ),
        )
    allowed = {s.span_id: s for s in evidence}
    reqs = {r.requirement_id: r for r in requirements.requirements}
    nodes = {n.node_id: (b, n) for b in bundles for n in b.structures}
    result: list[GapValidation] = []
    for target in target_map.values():
        proposal = proposed.get(target.gap_id)
        review = reviewed.get(target.gap_id)
        requirement_id = target.requirement_id or (
            None if proposal is None else proposal.requirement_id
        )
        req = reqs.get(requirement_id or "")
        reasons: list[str] = []
        valid = not duplicate
        if ref.project_id != requirements.request_ref.project_id or any(
            s.project_id != ref.project_id for s in evidence
        ):
            valid = False
            reasons.append("GAP_PROJECT_MISMATCH")
        if duplicate:
            reasons.append("DUPLICATE_GAP_ID")
        if req is None:
            valid = False
            reasons.append("GAP_REQUIREMENT_UNBOUND")
        if proposal is None or review is None:
            valid = False
            reasons.append("EXPLICIT_GAP_REVIEW_MISSING")
        span_refs = () if proposal is None else proposal.proposed_span_refs
        node_refs = () if proposal is None else proposal.proposed_structure_refs
        if (
            proposal is not None
            and target.requirement_id is not None
            and proposal.requirement_id != target.requirement_id
        ):
            valid = False
            reasons.append("GAP_REQUIREMENT_MISMATCH")
        refs = set(
            (
                *span_refs,
                *target.anchor_span_refs,
                *(() if proposal is None else proposal.anchor_span_refs),
                *(() if review is None else review.basis_span_refs),
            )
        )
        if refs - allowed.keys() or set(node_refs) - nodes.keys():
            valid = False
            reasons.append("GAP_REFERENCE_INVALID")
        selected = {s: allowed[s] for s in span_refs if s in allowed}
        versions = {s.artifact_id: s.source_version_id for s in selected.values()}
        if (
            proposal is not None
            and proposal.source_version_ids
            and set(proposal.source_version_ids) != set(versions.values())
        ):
            valid = False
            reasons.append("GAP_SOURCE_VERSION_MISMATCH")
        structure_digests: dict[str, str] = {}
        relations: list[StructuralRelation] = []
        for identifier in node_refs:
            if identifier not in nodes:
                continue
            bundle, node = nodes[identifier]
            if versions.get(bundle.artifact_id) != bundle.source_version_id:
                valid = False
                reasons.append("GAP_STRUCTURE_SOURCE_MISMATCH")
            structure_digests[identifier] = domain_digest(
                "GAP_STRUCTURE", "1.0.0", canonical_payload(node)
            )
            for relation in node.relations:
                target_node = nodes.get(relation.target_id)
                if target_node is None or (
                    target_node[0].artifact_id,
                    target_node[0].source_version_id,
                ) != (bundle.artifact_id, bundle.source_version_id):
                    valid = False
                    reasons.append("GAP_STRUCTURE_RELATION_TARGET_INVALID")
            relations.extend(node.relations)
        required = set(target.required_relation_kinds) | set(
            () if proposal is None else proposal.required_relation_kinds
        )
        anchor_spans = [allowed[s] for s in target.anchor_span_refs if s in allowed]
        if required and anchor_spans:
            anchor_sources = {(s.artifact_id, s.source_version_id) for s in anchor_spans}
            if any(
                (b.artifact_id, b.source_version_id) not in anchor_sources
                for n in node_refs
                if n in nodes
                for b in (nodes[n][0],)
            ):
                valid = False
                reasons.append("GAP_ANCHOR_SOURCE_MISMATCH")
            anchor_containers: set[str] = set()
            for span in anchor_spans:
                current = span.locator.structural_node_id
                seen_nodes: set[str] = set()
                while current is not None and current in nodes and current not in seen_nodes:
                    seen_nodes.add(current)
                    node = nodes[current][1]
                    if node.kind == "TABLE":
                        anchor_containers.add(current)
                    anchor_containers.update(
                        r.target_id
                        for r in node.relations
                        if r.target_id in nodes and nodes[r.target_id][1].kind == "TABLE"
                    )
                    current = node.parent_id
            if anchor_containers and any(
                r.kind in required and r.target_id not in anchor_containers for r in relations
            ):
                valid = False
                reasons.append("GAP_ANCHOR_RELATION_MISMATCH")
        if required - {r.kind for r in relations}:
            valid = False
            reasons.append("GAP_STRUCTURE_RELATION_MISSING")
        inferred = any(r.basis == "INFERRED" for r in relations)
        if inferred and (review is None or not review.relations_confirmed):
            valid = False
            reasons.append("INFERRED_RELATION_NOT_REVIEWED")
        if review is not None and review.requirement_set_digest != ref.revision_digest:
            valid = False
            reasons.append("GAP_REQUIREMENT_BASIS_STALE")
        disposition = "UNRESOLVED"
        semantic = "INCONCLUSIVE"
        if review is not None and review.verdict == "REJECTED":
            semantic = "REVIEWED_REJECTED"
        if (
            valid
            and proposal is not None
            and review is not None
            and req is not None
            and review.verdict == "APPLIED"
            and review.disposition == proposal.proposed_disposition
        ):
            if (
                not selected
                or not review.basis_span_refs
                or not set(review.basis_span_refs) <= set(selected)
            ):
                reasons.append("GAP_RESOLUTION_EVIDENCE_MISSING")
            elif req.kind == "BOUND_OBLIGATION" and review.disposition == "NOT_REQUIRED":
                reasons.append("BOUND_GAP_CANNOT_BE_WAIVED")
            else:
                disposition = review.disposition
                semantic = "REVIEWED_APPLIED"
        result.append(
            GapValidation(
                request_ref=requirements.request_ref,
                requirement_set_ref=ref,
                gap_id=target.gap_id,
                requirement_id=requirement_id,
                effective_disposition=disposition,
                structural_result="MATCH"
                if valid
                else "INVALID"
                if any("MISMATCH" in r or "INVALID" in r or "STALE" in r for r in reasons)
                else "MISSING",
                semantic_result=semantic,
                span_digests={k: s.text_sha256 for k, s in selected.items()},
                source_versions=versions,
                structure_digests=structure_digests,
                relation_bases=tuple(sorted({r.basis for r in relations})),
                reasons=tuple(dict.fromkeys(reasons)),
                reviewer_run=run,
            )
        )
    return tuple(result)
