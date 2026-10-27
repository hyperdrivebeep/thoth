"""Case-sensitive literal matching over two explicit immutable extracted-source scopes."""

import hashlib
import re
from datetime import datetime
from typing import Literal

from thoth.domain.absence_claim import (
    HANGUL_SOURCE_SUFFIXES,
    MAX_ABSENCE_CHARS,
    MAX_ABSENCE_CLAIMS,
    AbsenceAssessments,
    AbsenceClaim,
    AbsenceClaimAssessment,
    AbsenceScope,
    AbsenceScopeResult,
    AbsenceSourceRef,
    ReviewProposalE2,
    absence_check_digest,
    effective_absence,
)
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.research_reference import RevisionRef


def make_absence_scope(
    request: RevisionRef,
    kind: Literal["ACTUAL_ROLE_INPUT", "ALLOWED_EXTRACTED_AT_REQUEST"],
    source: tuple[EvidenceSpan, ...],
    *,
    captured_at: datetime,
    phase: str,
    policy_ref: str,
    cutoff_at: datetime,
    policy_digest: str,
    current: tuple[EvidenceSpan, ...] | None = None,
    stage_ref: RevisionRef | None = None,
    reasons: tuple[str, ...] = (),
    admitted_refs: frozenset[str] | None = None,
) -> AbsenceScope:
    available = (
        {s.span_id: s for s in source if current is None}
        if current is None
        else {s.span_id: s for s in current}
    )
    valid = tuple(
        s.span_id
        for s in source
        if (admitted_refs is None or s.span_id in admitted_refs)
        and available.get(s.span_id) == s
        and hashlib.sha256(s.exact_text.encode()).hexdigest() == s.text_sha256
    )
    issues = tuple(
        dict.fromkeys(
            (
                *reasons,
                *(("ABSENCE_EMPTY_SCOPE",) if not source else ()),
                *(
                    ("ABSENCE_SOURCE_BASIS_CHANGED",)
                    if len(valid)
                    != sum(admitted_refs is None or s.span_id in admitted_refs for s in source)
                    else ()
                ),
            )
        )
    )
    fields = {
        "request_ref": request,
        "kind": kind,
        "capture_phase": phase,
        "captured_at": captured_at,
        "role": "SEMANTIC_REVIEWER" if kind == "ACTUAL_ROLE_INPUT" else None,
        "stage_ref": stage_ref,
        "policy_ref": policy_ref,
        "cutoff_at": cutoff_at,
        "retrieval_policy_digest": policy_digest,
        "members": tuple(AbsenceSourceRef.from_span(s) for s in source),
        "checked_refs": valid,
        "scan_complete": not issues and len(valid) == len(source),
        "reasons": issues,
    }
    return AbsenceScope.model_validate(
        {
            **fields,
            "scope_digest": domain_digest("ABSENCE_SCOPE", "1.0.0", canonical_payload(fields)),
        }
    )


def literal_present(target: str, text: str) -> bool:
    # Exact spelling/case. A longer identifier, decimal/version or path is not the target.
    left = r"(?<![\w./\\:-])" if target[0].isalnum() or target[0] == "_" else ""
    if "가" <= target[0] <= "힣" and "가" <= target[-1] <= "힣":
        left = r"(?<![가-힣])"
    right = r"(?![\w]|[./\\:-](?=\w))" if target[-1].isalnum() or target[-1] == "_" else ""
    if "가" <= target[-1] <= "힣":
        # Inspect the source suffix in full; never trim or transform the declared target.
        for match in re.finditer(left + re.escape(target), text):
            end = match.end()
            while end < len(text) and "가" <= text[end] <= "힣":
                end += 1
            suffix = text[match.end() : end]
            if suffix in HANGUL_SOURCE_SUFFIXES or (
                not suffix and re.compile(r"(?!\w)").match(text, match.end()) is not None
            ):
                return True
        return False
    return re.search(left + re.escape(target) + right, text) is not None


def _unsupported(claim: AbsenceClaim) -> str | None:
    if claim.target_language_basis != "SOURCE_LITERAL":
        return "TARGET_NOT_SOURCE_LITERAL"
    if claim.kind == "CONCEPT":
        return "ABSENCE_CONCEPT_UNSUPPORTED"
    if not claim.target.strip() or claim.target != claim.target.strip():
        return "ABSENCE_TARGET_AMBIGUOUS"
    # A model's LITERAL label cannot turn an attached identifier suffix into an exact identifier.
    # This checks the declared string's syntax; it never infers the language of source material.
    if (claim.kind == "IDENTIFIER" or not any(c.isspace() for c in claim.target)) and re.search(
        r"[A-Za-z0-9_./\\:-](?:에서|으로|로|을|를|은|는|이|가|에|와|과)$", claim.target
    ):
        return "ABSENCE_IDENTIFIER_SUFFIX_AMBIGUOUS"
    return None


def _check(
    claim: AbsenceClaim,
    scope: AbsenceScope,
    source: tuple[EvidenceSpan, ...],
    not_checked: str | None,
) -> AbsenceScopeResult:
    if not_checked:
        return AbsenceScopeResult(
            scope_digest=scope.scope_digest, outcome="NOT_CHECKED", reason=not_checked
        )
    unsupported = _unsupported(claim)
    if unsupported:
        return AbsenceScopeResult(
            scope_digest=scope.scope_digest, outcome="UNSUPPORTED", reason=unsupported
        )
    permitted = set(scope.checked_refs)
    # Presence needs one exact witness; do not turn every repeated occurrence into a source use.
    witness = next(
        (
            s.span_id
            for s in source
            if s.span_id in permitted and literal_present(claim.target, s.exact_text)
        ),
        None,
    )
    witnesses = () if witness is None else (witness,)
    if witnesses:
        return AbsenceScopeResult(
            scope_digest=scope.scope_digest, outcome="FOUND", witness_refs=witnesses
        )
    if not scope.scan_complete:
        return AbsenceScopeResult(
            scope_digest=scope.scope_digest,
            outcome="NOT_CHECKED",
            reason=scope.reasons[0] if scope.reasons else "ABSENCE_SCOPE_INCOMPLETE",
        )
    return AbsenceScopeResult(scope_digest=scope.scope_digest, outcome="NOT_FOUND_WITHIN_SCOPE")


def _scope_input_error(scope: AbsenceScope, source: tuple[EvidenceSpan, ...]) -> str | None:
    actual = {s.span_id: s for s in source}
    bound = {s.span_id: s for s in scope.members}
    if any(
        ref not in actual
        or AbsenceSourceRef.from_span(actual[ref]) != bound[ref]
        or hashlib.sha256(actual[ref].exact_text.encode()).hexdigest() != actual[ref].text_sha256
        for ref in scope.checked_refs
    ):
        return "ABSENCE_SCAN_INPUT_MISMATCH"
    return None


def claim_origin(proposal: ReviewProposalE2, claim: AbsenceClaim) -> tuple[str, bool]:
    if claim.origin == "ANSWER":
        return "/answer", bool(claim.statement and claim.statement in proposal.answer)
    index = claim.finding_index
    return f"/confirmed_findings/{index}/statement", bool(
        index is not None
        and index < len(proposal.confirmed_findings)
        and claim.statement == proposal.confirmed_findings[index].statement
    )


def has_checkable_absence_claims(proposal: ReviewProposalE2) -> bool:
    return any(
        max(len(claim.target), len(claim.statement)) <= MAX_ABSENCE_CHARS
        and claim_origin(proposal, claim)[1]
        and _unsupported(claim) is None
        for claim in proposal.absence_claims[:MAX_ABSENCE_CLAIMS]
    )


def assess_absence_claims(
    request: RevisionRef,
    proposal: ReviewProposalE2,
    stage: RevisionRef,
    a: AbsenceScope,
    a_spans: tuple[EvidenceSpan, ...],
    b: AbsenceScope,
    b_spans: tuple[EvidenceSpan, ...],
) -> AbsenceAssessments:
    items: list[AbsenceClaimAssessment] = []
    a_error, b_error = _scope_input_error(a, a_spans), _scope_input_error(b, b_spans)
    for index, claim in enumerate(proposal.absence_claims):
        pointer, bound = claim_origin(proposal, claim)
        reason = (
            "ABSENCE_CLAIM_LIMIT"
            if index >= MAX_ABSENCE_CLAIMS
            else "ABSENCE_CHARACTER_LIMIT"
            if max(len(claim.target), len(claim.statement)) > MAX_ABSENCE_CHARS
            else "ABSENCE_ORIGIN_UNBOUND"
            if not bound
            else None
        )
        ar, br = (
            _check(claim, a, a_spans, reason or a_error),
            _check(claim, b, b_spans, reason or b_error),
        )
        effective = effective_absence(ar.outcome, br.outcome)
        missing = (
            set[str]()
            if ar.outcome == "FOUND"
            else set(br.witness_refs) - {s.span_id for s in a_spans}
        )
        fields: dict[str, object] = {
            "claim_id": f"{stage.revision_id}:{index}",
            "claim": claim,
            "claim_digest": domain_digest("ABSENCE_CLAIM", "1.0.0", canonical_payload(claim)),
            "origin_stage_ref": stage,
            "origin_pointer": pointer,
            "origin_bound": bound,
            "a_result": ar,
            "b_result": br,
            "effective": effective,
            "warning_code": effective if effective.startswith("FOUND_") else None,
            "injection_state": "PENDING" if missing else "NOT_NEEDED",
        }
        items.append(
            AbsenceClaimAssessment.model_validate(
                {**fields, "check_digest": absence_check_digest(fields)}
            )
        )
    return AbsenceAssessments(
        request_ref=request,
        active_stage_ref=stage,
        scope_sets=(a, b),
        items=tuple(items),
        not_checked_count=sum(
            i.a_result.outcome == "NOT_CHECKED" or i.b_result.outcome == "NOT_CHECKED"
            for i in items
        ),
        reason_codes=tuple(
            sorted(
                {
                    *a.reasons,
                    *b.reasons,
                    *(r.reason for i in items for r in (i.a_result, i.b_result) if r.reason),
                }
            )
        ),
    )
