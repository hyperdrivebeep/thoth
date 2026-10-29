"""Artifact-count routing uses the existing frame; prepared dense scores never widen it."""

from dataclasses import replace
from typing import TYPE_CHECKING

from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.evidence import connected_retrieval_spans
from thoth.domain.retrieval_embedding import DenseUnavailable, RoutingObservation
from thoth.domain.retrieval_fusion import FusedChunk, admit_chunks, reciprocal_rank_fusion
from thoth.domain.retrieval_routing_policy import RetrievalRoutingPolicy

if TYPE_CHECKING:
    from thoth.domain.retrieval_lane import LaneSelection, RetrievalRequest


def select_routed(request: "RetrievalRequest", policy: RetrievalRoutingPolicy) -> "LaneSelection":
    from thoth.domain.behavior_policy import RetrievalBehaviorPolicy
    from thoth.domain.retrieval_lane import (
        LANES,
        LaneSelection,
        full_catalog,
        select_retrieval_context,
    )

    eligible = connected_retrieval_spans(request.evidence)
    if len({s.project_id for s in eligible}) > 1:
        raise ValueError("DENSE_PROJECT_SCOPE_MISMATCH")
    count = len({s.artifact_id for s in eligible})
    if count <= 1:
        selected = select_retrieval_context(
            request,
            RetrievalBehaviorPolicy(
                max_spans=policy.max_spans,
                character_budget=policy.character_budget,
            ),
        )
        return replace(
            selected,
            routing=RoutingObservation(
                artifact_count=count,
                route="LEXICAL",
                dense_used=False,
            ),
        )
    complete, limitations = full_catalog(eligible, request.catalog, policy)
    if complete is not None:
        return LaneSelection(
            complete,
            "FULL_ELIGIBLE_EXTRACTED_CATALOG",
            eligible,
            limitations,
            routing=RoutingObservation(
                artifact_count=count, route="FULL_CATALOG", dense_used=False
            ),
        )
    lanes = [LANES[name](request, eligible) for name in ("LEXICAL_SUBSTRING_V1", "GROUPED_BM25")]
    fallback = None
    try:
        lanes.append(LANES["DENSE"](request, eligible))
    except DenseUnavailable as exc:
        fallback = str(exc)
    pinned = set(request.pinned_refs)
    selected = [s for s in eligible if s.span_id in pinned][: policy.max_spans]
    by_id = {s.span_id: s for s in eligible}
    ranks = [tuple(s.span_id for s in lane if s.span_id not in pinned) for lane in lanes]
    ordered = reciprocal_rank_fusion(ranks)
    admit_chunks(
        tuple(FusedChunk(0, (by_id[s],)) for s in ordered),
        selected,
        policy.max_spans,
        policy.character_budget,
    )
    observation = RoutingObservation(
        artifact_count=count,
        route="RRF_LEX_GB_DENSE" if fallback is None else "RRF_LEX_GB",
        dense_used=fallback is None,
        fallback_reason=fallback,
        dense_material_digest=None
        if request.dense is None
        else domain_digest(
            "DENSE_MATERIAL",
            "1.0.0",
            canonical_payload(request.dense),
        ),
    )
    provenance = (
        ("routing", "eligible-artifact-count:1.0.0"),
        ("fusion", "RRF"),
        ("k", "60"),
        (
            "route_digest",
            domain_digest(
                "RETRIEVAL_ROUTE",
                "1.0.0",
                canonical_payload(observation),
            ),
        ),
    )
    observed = request.catalog is not None and request.catalog.all_observed
    return LaneSelection(
        tuple(selected),
        "LEXICAL" if observed else "LEGACY",
        eligible,
        limitations,
        provenance,
        routing=observation,
    )
