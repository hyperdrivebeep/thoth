"""Policy 4 fusion selection, retaining the shared eligibility and admission frame."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from thoth.domain.behavior_policy import RetrievalFusionPolicy
from thoth.domain.evidence import connected_retrieval_spans
from thoth.domain.glossary_lane import check_glossary_pin, glossary_detail
from thoth.domain.retrieval_fusion import (
    FUSION_METHOD,
    FUSION_VERSION,
    RankedLane,
    admit_chunks,
    lane_contributions,
    merge_round_robin,
)

if TYPE_CHECKING:
    from thoth.domain.retrieval_lane import LaneSelection, RetrievalRequest


def select_fused(request: RetrievalRequest, policy: RetrievalFusionPolicy) -> LaneSelection:
    """Policy 4.0.0: each lane ranks the same eligible spans, then the merge and the frame admit."""
    from thoth.domain.retrieval_lane import (
        LANE_PROVENANCE,
        LANE_VERSIONS,
        LANES,
        LaneSelection,
        full_catalog,
        glossary_texts,
        lane_chunks,
    )

    for spec in policy.lanes:
        if spec.lane_id not in LANES:
            raise ValueError("RETRIEVAL_LANE_UNKNOWN")
        if LANE_VERSIONS.get(spec.lane_id) != spec.lane_version:
            raise ValueError("RETRIEVAL_LANE_VERSION_UNKNOWN")
        check_glossary_pin(spec, request.glossary)
    eligible = connected_retrieval_spans(request.evidence)
    complete, limitations = full_catalog(eligible, request.catalog, policy)
    if complete is not None:
        return LaneSelection(complete, "FULL_ELIGIBLE_EXTRACTED_CATALOG", eligible, limitations)
    pinned = set(request.pinned_refs)
    selected = [span for span in eligible if span.span_id in pinned][: policy.max_spans]
    ranked = [
        RankedLane(
            spec.lane_id,
            spec.lane_version,
            spec.depth,
            lane_chunks(
                spec.lane_id,
                request
                if spec.glossary is None
                else replace(request, glossary_min_confidence=spec.glossary.min_confidence),
                eligible,
            ),
        )
        for spec in policy.lanes
    ]
    credited = admit_chunks(
        merge_round_robin(ranked), selected, policy.max_spans, policy.character_budget
    )
    provenance: list[tuple[str, str]] = [
        ("fusion", FUSION_METHOD),
        ("fusion_version", FUSION_VERSION),
    ]
    for index, lane in enumerate(ranked):
        provenance.append(
            (f"lane{index}", f"{lane.lane_id}:{lane.lane_version}:depth={lane.depth}")
        )
        provenance.extend(
            (f"lane{index}.{key}", value) for key, value in LANE_PROVENANCE.get(lane.lane_id, ())
        )
    detail, glossary_keys = glossary_detail(policy.lanes, request.glossary, glossary_texts(request))
    provenance.extend(glossary_keys)
    observed = request.catalog is not None and request.catalog.all_observed
    return LaneSelection(
        tuple(selected),
        "LEXICAL" if observed else "LEGACY",
        eligible,
        limitations,
        tuple(provenance),
        lane_contributions(ranked, selected, credited),
        detail,
    )
