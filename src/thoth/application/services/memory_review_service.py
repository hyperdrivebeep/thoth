from __future__ import annotations

from collections.abc import Callable

from pydantic import AwareDatetime

from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.enums import MemoryKind
from thoth.domain.memory import (
    MemoryReviewContext,
    MemoryReviewRole,
    MemoryReviewVerdict,
    MemoryRoleReview,
)
from thoth.ports.memory import MemoryReviewerPort

_RANK = {
    MemoryReviewVerdict.PASS: 0,
    MemoryReviewVerdict.REVISE: 1,
    MemoryReviewVerdict.HOLD: 2,
    MemoryReviewVerdict.QUARANTINE: 3,
}


class MemoryReviewService:
    def __init__(self, reviewer: MemoryReviewerPort | None) -> None:
        self._reviewer = reviewer

    async def evaluate(
        self,
        *,
        candidate_digest: str,
        owner_revision_ref: str,
        source_ref: str | None,
        content_excerpt: str,
        kind: MemoryKind,
        cutoff_at: AwareDatetime,
        scope: dict[str, str],
        unsafe: bool,
        owner_valid: bool,
        content_reusable: bool,
        conflict: bool,
        validate_current: Callable[[], None] | None = None,
    ) -> tuple[MemoryRoleReview, ...]:
        deterministic = self._deterministic_reviews(
            candidate_digest=candidate_digest,
            unsafe=unsafe,
            owner_valid=owner_valid,
            content_reusable=content_reusable,
            conflict=conflict,
        )
        if self._reviewer is None:
            return deterministic
        final: list[MemoryRoleReview] = []
        for base in deterministic:
            if validate_current is not None:
                validate_current()
            context = self._context(
                role=base.role,
                candidate_digest=candidate_digest,
                owner_revision_ref=owner_revision_ref,
                source_ref=source_ref,
                content_excerpt=content_excerpt,
                kind=kind,
                cutoff_at=cutoff_at,
                scope=scope,
                unsafe=unsafe,
                owner_valid=owner_valid,
                content_reusable=content_reusable,
                conflict=conflict,
                prior=tuple(final),
            )
            try:
                candidate = await self._reviewer.review(context)
            except Exception:
                candidate = MemoryRoleReview(
                    role=base.role,
                    verdict=MemoryReviewVerdict.HOLD,
                    reason_code="MODEL_REVIEW_REQUIRED_ROLE_FAILED",
                    basis_digest=domain_digest(
                        "FAILED_MEMORY_REVIEW_BASIS",
                        "1.0.0",
                        canonical_payload(
                            {"role": base.role, "context_digest": context.context_digest}
                        ),
                    ),
                )
            final.append(self._apply_deterministic_override(base, candidate, context))
        return tuple(final)

    def _context(
        self,
        *,
        role: MemoryReviewRole,
        candidate_digest: str,
        owner_revision_ref: str,
        source_ref: str | None,
        content_excerpt: str,
        kind: MemoryKind,
        cutoff_at: AwareDatetime,
        scope: dict[str, str],
        unsafe: bool,
        owner_valid: bool,
        content_reusable: bool,
        conflict: bool,
        prior: tuple[MemoryRoleReview, ...],
    ) -> MemoryReviewContext:
        allowed = self._reviewer.context_fields(role) if self._reviewer is not None else ()
        values: dict[str, object] = {
            "owner_revision_ref": owner_revision_ref,
            "authority": "AUTHORITATIVE" if owner_valid else "UNKNOWN",
            "cutoff": cutoff_at,
            "source": source_ref,
            "bounded_content": None if unsafe else content_excerpt[:4_000],
            "reusability": content_reusable,
            "scope": scope,
            "outcome": kind.value,
            "limitations": "SEMANTIC_TRUTH_NOT_CERTIFIED",
            "alternative_explanations": conflict,
            "uncertainty": not owner_valid,
            "counterevidence": source_ref,
            "independent_verdicts": tuple(item.verdict.value for item in prior),
            "conflicts": conflict,
            "action_eligibility": kind == MemoryKind.FACT,
        }
        fields = {
            "unsafe": unsafe,
            "missing": not owner_valid or not content_reusable,
            "conflict": conflict,
            **{name: values.get(name) for name in allowed},
        }
        draft = {
            "role": role.value,
            "candidate_digest": candidate_digest,
            "fields": fields,
            "bounded": True,
        }
        return MemoryReviewContext.model_validate(
            {
                **draft,
                "context_digest": domain_digest(
                    "MEMORY_REVIEW_CONTEXT", "1.0.0", canonical_payload(draft)
                ),
            }
        )

    @staticmethod
    def _apply_deterministic_override(
        base: MemoryRoleReview,
        candidate: MemoryRoleReview,
        context: MemoryReviewContext,
    ) -> MemoryRoleReview:
        selected = base if _RANK[base.verdict] > _RANK[candidate.verdict] else candidate
        return selected.model_copy(
            update={
                "role": base.role,
                "reason_code": (
                    f"DETERMINISTIC_OVERRIDE_{base.reason_code}"
                    if selected is base and candidate.verdict != base.verdict
                    else selected.reason_code
                ),
                "basis_digest": domain_digest(
                    "REDUCED_MEMORY_ROLE_REVIEW",
                    "1.0.0",
                    canonical_payload(
                        {
                            "role": base.role,
                            "context_digest": context.context_digest,
                            "deterministic_basis": base.basis_digest,
                            "candidate_basis": candidate.basis_digest,
                            "selected_verdict": selected.verdict,
                        }
                    ),
                ),
                "institutionally_independent": False,
                "model_id": candidate.model_id,
                "prompt_version": candidate.prompt_version,
                "model_input_digest": candidate.model_input_digest,
                "model_output_digest": candidate.model_output_digest,
                "schema_digest": candidate.schema_digest,
                "scripted": candidate.scripted,
            }
        )

    @staticmethod
    def _deterministic_reviews(
        *,
        candidate_digest: str,
        unsafe: bool,
        owner_valid: bool,
        content_reusable: bool,
        conflict: bool,
    ) -> tuple[MemoryRoleReview, ...]:
        values = (
            (
                MemoryReviewRole.FACTS,
                MemoryReviewVerdict.QUARANTINE
                if unsafe
                else MemoryReviewVerdict.HOLD
                if not owner_valid
                else MemoryReviewVerdict.PASS,
                "UNSAFE_CONTENT"
                if unsafe
                else "OWNER_REVISION_MISSING"
                if not owner_valid
                else "OWNER_REVISION_AND_PROJECT_VERIFIED",
            ),
            (
                MemoryReviewRole.REFLECTION,
                MemoryReviewVerdict.QUARANTINE
                if unsafe
                else MemoryReviewVerdict.REVISE
                if not content_reusable
                else MemoryReviewVerdict.PASS,
                "UNSAFE_CONTENT"
                if unsafe
                else "REUSABLE_CONTENT_MISSING"
                if not content_reusable
                else "REUSABLE_SCOPE_IDENTIFIED",
            ),
            (
                MemoryReviewRole.DREAM,
                MemoryReviewVerdict.QUARANTINE
                if unsafe
                else MemoryReviewVerdict.HOLD
                if conflict
                else MemoryReviewVerdict.PASS,
                "UNSAFE_CONTENT"
                if unsafe
                else "ALTERNATIVE_CONFLICT_OPEN"
                if conflict
                else "ALTERNATIVE_TRACK_SEPARATED",
            ),
            (
                MemoryReviewRole.TEAM,
                MemoryReviewVerdict.QUARANTINE
                if unsafe
                else MemoryReviewVerdict.HOLD
                if not owner_valid or conflict
                else MemoryReviewVerdict.REVISE
                if not content_reusable
                else MemoryReviewVerdict.PASS,
                "UNSAFE_CONTENT"
                if unsafe
                else "AUTHORITY_OR_CONFLICT_HOLD"
                if not owner_valid or conflict
                else "CONTENT_REVISION_REQUIRED"
                if not content_reusable
                else "INDEPENDENT_REVIEWS_CONVERGED",
            ),
        )
        return tuple(
            MemoryRoleReview(
                role=role,
                verdict=verdict,
                reason_code=reason,
                basis_digest=domain_digest(
                    "MEMORY_ROLE_REVIEW",
                    "1.0.0",
                    canonical_payload(
                        {
                            "role": role,
                            "candidate_digest": candidate_digest,
                            "verdict": verdict,
                            "reason_code": reason,
                        }
                    ),
                ),
                scripted=True,
            )
            for role, verdict, reason in values
        )
