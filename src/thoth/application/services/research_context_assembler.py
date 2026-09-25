"""Expand an authorized source hit by recorded structure, bounded by the active retrieval policy."""

import hashlib
from time import perf_counter_ns

from thoth.application.services.research_retrieval import text_neighbors
from thoth.application.services.research_retrieval_policy import record_selection, retrieval_policy
from thoth.domain.artifact import StructuralDocument, StructuralNode
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.enums import CutoffState
from thoth.domain.enums import StructuralNodeKind as Kind
from thoth.domain.evidence import EvidenceSpan, connected_retrieval_spans
from thoth.domain.evidence_bundle import (
    ContextAssembly,
    ContextStructureRef,
    EvidenceContextBundle,
    InternalExpansionWave,
)
from thoth.ports.artifact_ledger import ArtifactLedgerPort


def _ancestors(node: StructuralNode, nodes: dict[str, StructuralNode]) -> list[StructuralNode]:
    result = [node]
    seen = {node.node_id}
    while result[-1].parent_id is not None:
        parent = nodes.get(result[-1].parent_id)
        if parent is None or parent.node_id in seen:
            raise ValueError("CONTEXT_STRUCTURE_INVALID")
        result.append(parent)
        seen.add(parent.node_id)
    return result


def _continuation_group(
    anchor_node: StructuralNode,
    nodes: dict[str, StructuralNode],
) -> tuple[str, tuple[StructuralNode, ...]] | None:
    root_id = next(
        (
            relation.target_id
            for relation in anchor_node.relations
            if relation.kind == "CONTINUATION_OF"
        ),
        anchor_node.node_id,
    )
    root = nodes.get(root_id)
    if root is None:
        raise ValueError("CONTEXT_STRUCTURE_INVALID")
    members = [
        node
        for node in nodes.values()
        if node.node_id == root_id
        or any(
            relation.kind == "CONTINUATION_OF" and relation.target_id == root_id
            for relation in node.relations
        )
    ]
    if len(members) <= 1 or anchor_node.node_id not in {node.node_id for node in members}:
        return None
    members.sort(key=lambda node: node.ordinal)
    return root.node_id, tuple(members)


def assemble_context(
    ranking: tuple[str, ...],
    candidates: tuple[EvidenceSpan, ...],
    source: tuple[EvidenceSpan, ...],
    artifacts: ArtifactLedgerPort,
) -> ContextAssembly:
    started = perf_counter_ns()
    policy = retrieval_policy()
    allowed = {s.span_id: s for s in connected_retrieval_spans(source)}
    candidate_ids = {s.span_id for s in candidates}
    if set(ranking) - candidate_ids or len(ranking) != len(set(ranking)):
        raise ValueError("RERANK_UNKNOWN_OR_DUPLICATE_ID")
    documents: dict[tuple[str, str, str], StructuralDocument | None] = {}
    selected: dict[str, EvidenceSpan] = {}
    structure_ids: set[str] = set()
    bundles: list[EvidenceContextBundle] = []
    omitted: list[str] = []
    missing: list[str] = []
    characters = 0
    for identifier in ranking:
        if identifier not in allowed:
            raise ValueError("CONTEXT_SOURCE_BASIS_MISMATCH")
        anchor = allowed[identifier]
        key = (anchor.project_id, anchor.artifact_id, anchor.source_version_id)
        if key not in documents:
            documents[key] = artifacts.read_structure(*key)
        document = documents[key]
        nodes = {} if document is None else {n.node_id: n for n in document.nodes}
        version_spans = [
            s for s in allowed.values() if (s.project_id, s.artifact_id, s.source_version_id) == key
        ]
        by_node = {
            s.locator.structural_node_id: s for s in version_spans if s.locator.structural_node_id
        }
        if not by_node and nodes:
            # Compatibility bridge only when locator+text is an exact unique match.
            for node in nodes.values():
                matches = [
                    s
                    for s in version_spans
                    if s.exact_text == node.text and s.locator == node.locator
                ]
                if len(matches) == 1:
                    by_node[node.node_id] = matches[0]
        anchor_node = nodes.get(anchor.locator.structural_node_id or "")
        if (
            document is not None
            and anchor.locator.structural_node_id is not None
            and anchor_node is None
        ):
            raise ValueError("CONTEXT_STRUCTURE_REFERENCE_INVALID")
        if anchor_node is None:
            anchor_node = next(
                (nodes[n] for n, s in by_node.items() if s.span_id == identifier), None
            )

        group_nodes, material, container, limitations = _expand_anchor(
            anchor, anchor_node, nodes, by_node, version_spans
        )
        if anchor_node is None:
            missing.append(identifier)
        needed = [s for k, s in material.items() if k not in selected]
        if any(hashlib.sha256(s.exact_text.encode()).hexdigest() != s.text_sha256 for s in needed):
            raise ValueError("CONTEXT_TEXT_DIGEST_MISMATCH")
        new_nodes = set(group_nodes) - structure_ids
        cost = sum(len(s.exact_text) for s in needed)
        if (
            len(selected) + len(needed) > policy.max_spans
            or len(structure_ids) + len(new_nodes) > policy.max_spans
            or characters + cost > policy.character_budget
        ):
            omitted.append(identifier)
            continue
        selected.update(material)
        structure_ids.update(group_nodes)
        characters += cost
        refs = tuple(
            ContextStructureRef(
                node_id=n.node_id,
                kind=n.kind.value,
                parent_id=n.parent_id,
                locator=n.locator,
                text_digest=None if n.text is None else hashlib.sha256(n.text.encode()).hexdigest(),
                relations=n.relations,
                warnings=n.extraction_warnings,
            )
            for n in sorted(group_nodes.values(), key=lambda n: n.ordinal)
        )
        basis = domain_digest(
            "EVIDENCE_CONTEXT_BUNDLE",
            "1.0.0",
            canonical_payload(
                {"source": key, "spans": tuple(material.values()), "structures": refs}
            ),
        )
        bundles.append(
            EvidenceContextBundle(
                bundle_id=f"bundle:{basis}",
                project_id=key[0],
                artifact_id=key[1],
                source_version_id=key[2],
                container_id=container,
                anchor_span_refs=(identifier,),
                span_refs=tuple(material),
                span_digests={k: s.text_sha256 for k, s in material.items()},
                structures=refs,
                basis_digest=basis,
                limitations=tuple(sorted(set(limitations))),
            )
        )
    merged: dict[tuple[str, str, str], EvidenceContextBundle] = {}
    for bundle in bundles:
        key = (bundle.artifact_id, bundle.source_version_id, bundle.container_id)
        previous = merged.get(key)
        if previous is None:
            merged[key] = bundle
            continue
        structures = {node.node_id: node for node in (*previous.structures, *bundle.structures)}
        span_refs = tuple(dict.fromkeys((*previous.span_refs, *bundle.span_refs)))
        anchors = tuple(dict.fromkeys((*previous.anchor_span_refs, *bundle.anchor_span_refs)))
        digest = domain_digest(
            "EVIDENCE_CONTEXT_BUNDLE",
            "1.0.0",
            canonical_payload(
                {
                    "source": key,
                    "spans": tuple(selected[ref] for ref in span_refs),
                    "structures": tuple(structures.values()),
                }
            ),
        )
        merged[key] = bundle.model_copy(
            update={
                "bundle_id": f"bundle:{digest}",
                "basis_digest": digest,
                "anchor_span_refs": anchors,
                "span_refs": span_refs,
                "span_digests": {ref: selected[ref].text_sha256 for ref in span_refs},
                "structures": tuple(structures.values()),
                "limitations": tuple(sorted(set((*previous.limitations, *bundle.limitations)))),
            }
        )
    result = tuple(selected.values())
    wave_basis = domain_digest(
        "INTERNAL_EXPANSION", "1.0.0", canonical_payload({"ranking": ranking, "source": source})
    )
    record_selection(
        "STRUCTURAL_INTERNAL_CONTEXT", {"ranking": ranking, "basis": wave_basis}, result, started
    )
    new_count = len(set(selected) - set(ranking))
    return ContextAssembly(
        evidence=result,
        bundles=tuple(merged.values()),
        wave=InternalExpansionWave(
            basis_digest=wave_basis,
            anchor_count=len(ranking),
            selected_count=len(result),
            new_information_count=new_count,
            omitted_anchor_refs=tuple(omitted),
            missing_structure_refs=tuple(missing),
            selected_bytes=sum(len(s.exact_text.encode()) for s in result),
            character_budget=policy.character_budget,
            node_budget=policy.max_spans,
            termination="POLICY_LIMIT"
            if omitted
            else "STRUCTURE_UNAVAILABLE"
            if missing
            else "NO_NEW_INFORMATION"
            if not new_count
            else "COMPLETE_LOCAL_GROUPS",
        ),
    )


def _expand_anchor(
    anchor: EvidenceSpan,
    anchor_node: StructuralNode | None,
    nodes: dict[str, StructuralNode],
    by_node: dict[str, EvidenceSpan],
    version_spans: list[EvidenceSpan],
) -> tuple[dict[str, StructuralNode], dict[str, EvidenceSpan], str, list[str]]:
    identifier = anchor.span_id
    group_nodes: dict[str, StructuralNode] = {}
    material: dict[str, EvidenceSpan] = {}
    container = identifier
    limitations: list[str] = []
    if anchor.cutoff_state == CutoffState.UNKNOWN_TIME:
        limitations.append("SOURCE_TIME_UNCONFIRMED")
    if anchor_node is not None:
        continuation = _continuation_group(anchor_node, nodes)
        continuation_ids: set[str] = set()
        relation_sources = (anchor_node,)
        if continuation is not None:
            container, continuation_nodes = continuation
            for node in continuation_nodes:
                group_nodes[node.node_id] = node
            continuation_ids = set(group_nodes)
            relation_sources = continuation_nodes
        own = _ancestors(anchor_node, nodes)
        tables = {n.node_id for n in own if n.kind == Kind.TABLE}
        for source_node in relation_sources:
            tables.update(
                relation.target_id
                for relation in source_node.relations
                if (target := nodes.get(relation.target_id)) is not None
                and target.kind == Kind.TABLE
            )
        if tables:
            container = "|".join(sorted(tables))
            rows = {
                ancestor.node_id
                for source_node in relation_sources
                for ancestor in _ancestors(source_node, nodes)
                if ancestor.kind == Kind.ROW
            }
            full_tables = any(
                source_node.kind in {Kind.TABLE, Kind.CAPTION, Kind.HEADER}
                for source_node in relation_sources
            )
            for node in nodes.values():
                chain = _ancestors(node, nodes)
                in_table = bool({n.node_id for n in chain} & tables)
                in_row = bool({n.node_id for n in chain} & rows)
                related = any(r.target_id in tables for r in node.relations)
                if (
                    (
                        in_table
                        and (
                            full_tables
                            or in_row
                            or node.kind in {Kind.TABLE, Kind.HEADER, Kind.UNIT}
                        )
                    )
                    or related
                    or node.node_id == anchor_node.node_id
                    or node.node_id in continuation_ids
                ):
                    group_nodes[node.node_id] = node
            for node in tuple(group_nodes.values()):
                group_nodes.update(
                    {
                        n.node_id: n
                        for n in _ancestors(node, nodes)
                        if n.kind in {Kind.TABLE, Kind.ROW}
                    }
                )
        else:
            if not group_nodes:
                group_nodes[anchor_node.node_id] = anchor_node
                limitations.append("NO_TABLE_RELATION_FOR_ANCHOR")
                material.update(
                    {s.span_id: s for s in text_neighbors(anchor, tuple(version_spans))}
                )
        for node in sorted(group_nodes.values(), key=lambda item: item.ordinal):
            span = by_node.get(node.node_id)
            if span is not None:
                if span.exact_text != node.text:
                    raise ValueError("CONTEXT_NODE_TEXT_MISMATCH")
                material[span.span_id] = span
            elif node.node_id in continuation_ids:
                limitations.append("CONTINUATION_SPAN_UNAVAILABLE")
            if any(r.basis == "INFERRED" for r in node.relations):
                limitations.append("RELATION_REQUIRES_SEMANTIC_REVIEW")
    else:
        limitations.append("STRUCTURE_UNAVAILABLE")
        material[identifier] = anchor
        material.update({s.span_id: s for s in text_neighbors(anchor, tuple(version_spans))})
    if identifier not in material:
        material[identifier] = anchor
    return group_nodes, material, container, limitations
