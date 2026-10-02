"""Pair the criteria of two results: the same id, kind, target and question mean the same one.

Requirement ids such as "check:0" are numbered afresh for every run, so an id alone says nothing
about whether two results asked the same thing. A criterion whose content differs is shown as
removed and added.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from thoth.domain.evidence_requirements import EvidenceRequirement
from thoth.domain.research_followup import CoverageMatrixRow, CriterionDelta, CriterionState


@dataclass(frozen=True)
class CriterionView:
    requirement: EvidenceRequirement
    row: CoverageMatrixRow


def _key(view: CriterionView) -> tuple[str, str, str, str]:
    requirement = view.requirement
    return (
        requirement.requirement_id,
        requirement.kind,
        requirement.target,
        requirement.question,
    )


def _state(view: CriterionView) -> CriterionState:
    row = view.row
    return CriterionState(
        status=row.status,
        relation=row.relation,
        validation=row.validation,
        blocker=row.blocker,
    )


def pair_criteria(
    before: Sequence[CriterionView], after: Sequence[CriterionView]
) -> tuple[CriterionDelta, ...]:
    earlier = {_key(view): view for view in before}
    later = {_key(view): view for view in after}
    deltas: list[CriterionDelta] = []
    for key, view in later.items():
        match = earlier.get(key)
        if match is None:
            deltas.append(_one_sided(view, "ADDED"))
            continue
        old, new = _state(match), _state(view)
        old_refs, new_refs = match.row.evidence_refs, view.row.evidence_refs
        added = tuple(ref for ref in new_refs if ref not in old_refs)
        removed = tuple(ref for ref in old_refs if ref not in new_refs)
        unchanged = old == new and not added and not removed
        deltas.append(
            CriterionDelta(
                requirement_id=key[0],
                target=key[2],
                question=key[3],
                match="SAME",
                before=old,
                after=new,
                evidence_refs_added=added,
                evidence_refs_removed=removed,
                unchanged_hold=unchanged and new.status != "SATISFIED",
            )
        )
    deltas.extend(_one_sided(view, "REMOVED") for key, view in earlier.items() if key not in later)
    return tuple(deltas)


def _one_sided(view: CriterionView, match: str) -> CriterionDelta:
    state = _state(view)
    added = match == "ADDED"
    return CriterionDelta(
        requirement_id=view.requirement.requirement_id,
        target=view.requirement.target,
        question=view.requirement.question,
        match="ADDED" if added else "REMOVED",
        before=None if added else state,
        after=state if added else None,
    )
