"""Merge the rankings of several lanes into one order; pure, deterministic, no scores.

ROUND_ROBIN_DEDUP 1.0.0: every lane keeps only the first `depth` chunks of its own ranking, then the
lanes take turns in the order the policy lists them, one chunk each, rank by rank. A chunk is one
span for a span lane and one structure group for GROUPED_BM25. Nothing is summed or compared, so
there are no ties: the order of the policy is the tie-break. A lane that runs out of chunks is
skipped and the others go on.

The merged order is then admitted under the shared frame's span and character budget. A span that
was already admitted (pinned or from an earlier chunk) is skipped. The first span of a chunk that
does not fit ends that chunk, so a group is never admitted with a hole in the middle; the next
chunk is still tried, so one large group cannot keep out a small chunk that comes after it. A chunk
of one span is therefore simply skipped, as the single-lane frame does.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from thoth.domain.evidence import EvidenceSpan

FUSION_METHOD = "ROUND_ROBIN_DEDUP"
FUSION_VERSION = "1.0.0"

type Chunk = tuple[EvidenceSpan, ...]


@dataclass(frozen=True)
class RankedLane:
    """One lane's complete ranking, as chunks, with the depth its policy gives it."""

    lane_id: str
    lane_version: str
    depth: int
    chunks: tuple[Chunk, ...]


@dataclass(frozen=True)
class FusedChunk:
    lane_index: int
    spans: Chunk


@dataclass(frozen=True)
class LaneContribution:
    """What one lane put into a selection, kept for the evaluation output."""

    lane_id: str
    lane_version: str
    depth: int
    ranked: int
    credited_refs: tuple[str, ...]
    also_refs: tuple[str, ...]


def merge_round_robin(lanes: Sequence[RankedLane]) -> tuple[FusedChunk, ...]:
    """Rank by rank, in policy order; a lane's own ranking is cut to its depth first."""
    cut = [lane.chunks[: lane.depth] for lane in lanes]
    return tuple(
        FusedChunk(index, chunks[rank])
        for rank in range(max((len(chunks) for chunks in cut), default=0))
        for index, chunks in enumerate(cut)
        if rank < len(chunks)
    )


def admit_chunks(
    order: Sequence[FusedChunk],
    selected: list[EvidenceSpan],
    max_spans: int,
    character_budget: int,
) -> dict[str, int]:
    """Admit the merged order after the spans already in `selected`; return who first gave each.

    `selected` is extended in place. The result maps a span id to the index of the lane whose
    chunk admitted it; spans that were in `selected` to begin with (pins) are not in it.
    """
    taken = {span.span_id for span in selected}
    size = sum(len(span.exact_text) for span in selected)
    credited: dict[str, int] = {}
    for chunk in order:
        for span in chunk.spans:
            if span.span_id in taken:
                continue
            if len(selected) >= max_spans or size + len(span.exact_text) > character_budget:
                break
            selected.append(span)
            taken.add(span.span_id)
            credited[span.span_id] = chunk.lane_index
            size += len(span.exact_text)
    return credited


def lane_contributions(
    lanes: Sequence[RankedLane],
    selected: Sequence[EvidenceSpan],
    credited: dict[str, int],
) -> tuple[LaneContribution, ...]:
    """Per lane: the selected spans it admitted first, and those it also ranked within its depth."""
    result: list[LaneContribution] = []
    for index, lane in enumerate(lanes):
        within = {span.span_id for chunk in lane.chunks[: lane.depth] for span in chunk}
        mine = tuple(s.span_id for s in selected if credited.get(s.span_id) == index)
        also = tuple(
            s.span_id
            for s in selected
            if s.span_id in within and s.span_id in credited and credited[s.span_id] != index
        )
        result.append(
            LaneContribution(
                lane.lane_id, lane.lane_version, lane.depth, len(lane.chunks), mine, also
            )
        )
    return tuple(result)
