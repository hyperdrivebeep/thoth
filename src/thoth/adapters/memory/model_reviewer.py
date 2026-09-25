from __future__ import annotations

from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.memory import (
    MemoryReviewContext,
    MemoryReviewRole,
    MemoryReviewVerdict,
    MemoryRoleReview,
)
from thoth.ports.memory import MemoryReviewerPort

_FIELDS = {
    MemoryReviewRole.FACTS: (
        "owner_revision_ref",
        "authority",
        "cutoff",
        "source",
        "bounded_content",
    ),
    MemoryReviewRole.REFLECTION: (
        "reusability",
        "scope",
        "outcome",
        "limitations",
        "bounded_content",
    ),
    MemoryReviewRole.DREAM: ("alternative_explanations", "uncertainty", "counterevidence"),
    MemoryReviewRole.TEAM: ("independent_verdicts", "conflicts", "action_eligibility"),
}


class DeterministicRoleMemoryReviewer(MemoryReviewerPort):
    def context_fields(self, role: MemoryReviewRole) -> tuple[str, ...]:
        return _FIELDS[role]

    async def review(self, context: MemoryReviewContext) -> MemoryRoleReview:
        unsafe = context.fields.get("unsafe") is True
        missing = context.fields.get("missing") is True
        conflict = context.fields.get("conflict") is True
        verdict = (
            MemoryReviewVerdict.QUARANTINE
            if unsafe
            else MemoryReviewVerdict.HOLD
            if conflict
            else MemoryReviewVerdict.REVISE
            if missing
            else MemoryReviewVerdict.PASS
        )
        reason = {
            MemoryReviewVerdict.QUARANTINE: "ROLE_CONTEXT_UNSAFE",
            MemoryReviewVerdict.HOLD: "ROLE_CONTEXT_CONFLICT",
            MemoryReviewVerdict.REVISE: "ROLE_CONTEXT_INCOMPLETE",
            MemoryReviewVerdict.PASS: "ROLE_CONTEXT_PASS",
        }[verdict]
        return MemoryRoleReview(
            role=context.role,
            verdict=verdict,
            reason_code=reason,
            basis_digest=domain_digest(
                "SEMANTIC_MEMORY_REVIEW",
                "1.0.0",
                canonical_payload(
                    {
                        "role": context.role.value,
                        "candidate_digest": context.candidate_digest,
                        "context_digest": context.context_digest,
                        "verdict": verdict.value,
                        "reason": reason,
                    }
                ),
            ),
            scripted=True,
        )
