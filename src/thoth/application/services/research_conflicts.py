"""Reduce reviewed conflicts using exact requirement and current evidence identities."""

from collections import Counter

from thoth.domain.evidence_requirements import (
    RequirementSetRevision,
    ReviewAdjudication,
    ReviewProposal,
    ScopedConflictAssessment,
)
from thoth.domain.research_request import RevisionRef


def reduce_scoped_conflicts(
    requirements: RequirementSetRevision,
    basis: RevisionRef,
    proposal: ReviewProposal,
    adjudication: ReviewAdjudication,
    allowed: set[str],
) -> tuple[ScopedConflictAssessment, ...]:
    by_id = {item.requirement_id: item for item in requirements.requirements}
    all_targets = tuple(sorted({item.target for item in requirements.requirements}))
    decisions = {item.conflict_id: item for item in adjudication.conflict_decisions}
    counts = Counter(item.conflict_id for item in proposal.scoped_conflicts)
    decision_counts = Counter(item.conflict_id for item in adjudication.conflict_decisions)
    result: list[ScopedConflictAssessment] = []
    for candidate in proposal.scoped_conflicts:
        decision = decisions.get(candidate.conflict_id)
        known = bool(candidate.requirement_ids) and set(candidate.requirement_ids) <= by_id.keys()
        targets = (
            tuple(sorted({by_id[key].target for key in candidate.requirement_ids}))
            if known
            else all_targets
        )
        valid = bool(
            known
            and candidate.evidence_refs
            and set(candidate.evidence_refs) <= allowed
            and counts[candidate.conflict_id] == 1
            and decision_counts[candidate.conflict_id] == 1
            and decision
            and decision.verdict == "APPLIED"
            and decision.requirement_set_digest == basis.revision_digest
            and decision.basis_refs
            and set(decision.basis_refs) <= allowed
        )
        resolution = decision.resolution if valid and decision else "UNRESOLVED"
        # An optional proposal alone has no authority to release a target.
        blocked = () if resolution in {"RESOLVED", "NOT_RELEVANT"} else targets
        result.append(
            ScopedConflictAssessment(
                request_ref=requirements.request_ref,
                requirement_set_ref=basis,
                conflict_id=candidate.conflict_id,
                affected_targets=targets,
                evidence_refs=candidate.evidence_refs,
                resolution=resolution,
                validation="APPLIED" if valid else "NEEDS_REASSESSMENT",
                blocked_targets=blocked,
                next_check=candidate.description if blocked else "",
                candidate=candidate,
                decision=decision,
            )
        )
    # Historical free text has no reviewed scope. It must be reassessed rather than
    # being silently promoted as optional or parsed into a guessed target.
    for index, description in enumerate(proposal.conflicts):
        result.append(
            ScopedConflictAssessment(
                request_ref=requirements.request_ref,
                requirement_set_ref=basis,
                conflict_id=f"legacy:{index}",
                affected_targets=all_targets,
                evidence_refs=(),
                resolution="UNRESOLVED",
                validation="NEEDS_REASSESSMENT",
                blocked_targets=all_targets,
                next_check=description,
            )
        )
    return tuple(result)
