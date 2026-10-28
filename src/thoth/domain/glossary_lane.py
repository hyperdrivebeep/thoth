"""What the glossary lane needs around its ranking: the pin check, the query terms and the record.

The lane itself is registered in `retrieval_lane` (it ranks structure groups there); this module
holds the parts that read a policy's glossary pin and the matcher, so that module stays small. Pure.
"""

from collections.abc import Sequence

from thoth.domain.behavior_policy import FusedLaneSpec
from thoth.domain.glossary import (
    GLOSSARY_MATCHER_VERSION,
    Glossary,
    GlossaryConfidence,
    GlossaryDetail,
    match_glossary,
)
from thoth.domain.lane_scoring import BM25_VERSION, identifier_terms
from thoth.domain.query_identifiers import SCANNER_VERSION
from thoth.domain.structure_groups import GROUPING_VERSION

# The rules the lane's ranking depends on; a change of any of them is a new lane version.
GLOSSARY_LANE_PROVENANCE = (
    ("matcher", GLOSSARY_MATCHER_VERSION),
    ("grouping", GROUPING_VERSION),
    ("scoring", BM25_VERSION),
    ("scanner", SCANNER_VERSION),
)


def check_glossary_pin(spec: FusedLaneSpec, glossary: Glossary | None) -> None:
    """A glossary other than the one the policy was made for would select differently."""
    if spec.glossary is None:
        return
    if glossary is None:
        raise ValueError("RETRIEVAL_GLOSSARY_UNAVAILABLE")
    if glossary.digest.lower() != spec.glossary.digest.lower():
        raise ValueError("RETRIEVAL_GLOSSARY_DIGEST_MISMATCH")


def expansion_terms(
    texts: Sequence[str], glossary: Glossary | None, floor: GlossaryConfidence | None
) -> list[str]:
    """The query terms of the lane: the English terms the Korean terms of `texts` stand for."""
    if glossary is None or floor is None:
        raise ValueError("RETRIEVAL_GLOSSARY_UNAVAILABLE")
    expansions = match_glossary(texts, glossary, floor).expansions
    return [term for english in expansions for term in identifier_terms(english)]


def glossary_detail(
    lanes: Sequence[FusedLaneSpec], glossary: Glossary | None, texts: Sequence[str]
) -> tuple[GlossaryDetail | None, tuple[tuple[str, str], ...]]:
    """What the glossary lane of a fused policy used, and the provenance keys that say so."""
    for index, spec in enumerate(lanes):
        if spec.glossary is None or glossary is None:
            continue
        floor = spec.glossary.min_confidence
        matches = match_glossary(texts, glossary, floor)
        detail = GlossaryDetail(
            glossary.digest, floor, len(glossary.entries), glossary.verified, matches
        )
        fired = ",".join(entry_id for term in matches.fired for entry_id in term.entry_ids)
        return detail, (
            (f"lane{index}.glossary_digest", detail.digest),
            (f"lane{index}.min_confidence", floor),
            (f"lane{index}.glossary_entries", str(detail.entries_carried)),
            (f"lane{index}.glossary_verified", str(detail.verified).lower()),
            (f"lane{index}.fired", fired),
        )
    return None, ()
