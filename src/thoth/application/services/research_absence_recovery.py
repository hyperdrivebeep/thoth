"""Restore saved literal-check bindings for learning-only recovery, without re-running a model."""

import hashlib
from collections.abc import Callable
from datetime import datetime
from typing import cast

from thoth.application.services.research_absence_usage import (
    ABSENCE_SCOPE_USAGE_LIMIT,
    admit_absence_catalog,
)
from thoth.domain.absence_claim import (
    AbsenceAssessments,
    AbsenceClaimAssessment,
    AbsenceRun,
    AbsenceScope,
    AbsenceSourceRef,
    absence_check_digest,
    effective_absence,
)
from thoth.domain.canonical import canonical_payload, domain_digest, same_stored_instant
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.research_execution import ResearchWork
from thoth.ports.source_version_catalog import SourceVersionCatalogPort

RECOVERY_SCOPE_CHANGED = "ABSENCE_RECOVERY_SCOPE_BASIS_CHANGED"


def _restore_scope(
    scope: AbsenceScope,
    current: dict[str, EvidenceSpan],
    policy_ref: str,
    cutoff_at: datetime,
    admitted_refs: frozenset[str] | None = None,
    admission_reasons: tuple[str, ...] = (),
) -> AbsenceScope:
    members = {s.span_id: s for s in scope.members}
    valid = tuple(
        ref
        for ref in scope.checked_refs
        if (admitted_refs is None or ref in admitted_refs)
        and (span := current.get(ref)) is not None
        and AbsenceSourceRef.from_span(span) == members[ref]
        and hashlib.sha256(span.exact_text.encode()).hexdigest() == span.text_sha256
    )
    policy_matches = scope.policy_ref == policy_ref and same_stored_instant(
        scope.cutoff_at, cutoff_at
    )
    if valid == scope.checked_refs and policy_matches:
        return scope
    fields: dict[str, object] = {
        **scope.model_dump(exclude={"scope_digest"}),
        "checked_refs": valid if policy_matches else (),
        "scan_complete": False,
        "reasons": tuple(
            dict.fromkeys((*scope.reasons, RECOVERY_SCOPE_CHANGED, *admission_reasons))
        ),
    }
    return AbsenceScope.model_validate(
        {
            **fields,
            "scope_digest": domain_digest("ABSENCE_SCOPE", "1.0.0", canonical_payload(fields)),
        }
    )


def _restore_item(
    item: AbsenceClaimAssessment, scopes: dict[str, AbsenceScope], observed: AbsenceAssessments
) -> AbsenceClaimAssessment:
    a, b = item.a_result, item.b_result
    new_a, new_b = scopes[a.scope_digest], scopes[b.scope_digest]
    if a.scope_digest == new_a.scope_digest and b.scope_digest == new_b.scope_digest:
        return item
    if a.scope_digest != new_a.scope_digest:
        a = a.model_copy(
            update={
                "scope_digest": new_a.scope_digest,
                "outcome": "NOT_CHECKED",
                "witness_refs": (),
                "reason": ABSENCE_SCOPE_USAGE_LIMIT
                if ABSENCE_SCOPE_USAGE_LIMIT in new_a.reasons
                else RECOVERY_SCOPE_CHANGED,
            }
        )
    if b.scope_digest != new_b.scope_digest:
        b = b.model_copy(
            update={
                "scope_digest": new_b.scope_digest,
                "outcome": "NOT_CHECKED",
                "witness_refs": (),
                "reason": ABSENCE_SCOPE_USAGE_LIMIT
                if ABSENCE_SCOPE_USAGE_LIMIT in new_b.reasons
                else RECOVERY_SCOPE_CHANGED,
            }
        )
    effective = effective_absence(a.outcome, b.outcome)
    fields: dict[str, object] = {
        **item.model_dump(),
        "a_result": a,
        "b_result": b,
        "effective": effective,
        "warning_code": effective if effective.startswith("FOUND_") else None,
    }
    if item.injection_state == "PENDING":
        fields.update(injection_state="NOT_DELIVERED", injection_reason=RECOVERY_SCOPE_CHANGED)
    fields["check_digest"] = absence_check_digest(fields, checker_version=observed.checker_version)
    return AbsenceClaimAssessment.model_validate(fields)


def restore_absence_recovery(
    work: ResearchWork,
    result: dict[str, object],
    current: tuple[EvidenceSpan, ...],
    *,
    policy_ref: str,
    cutoff_at: datetime,
    read_evidence: Callable[[str], EvidenceSpan | None],
    catalog: SourceVersionCatalogPort | None = None,
) -> dict[str, object]:
    raw = result.get("absence_assessments")
    if raw is None:
        return result
    observed = AbsenceAssessments.model_validate(raw)
    if observed.request_ref != work.request_ref:
        raise ValueError("ABSENCE_RECOVERY_REQUEST_MISMATCH")
    # Revalidate the recorded members, not a newly selected pool. New sources cannot enlarge b.
    available = {s.span_id: s for s in current if s.project_id == work.request_ref.project_id}
    b_scopes = tuple(s for s in observed.scope_sets if s.kind == "ALLOWED_EXTRACTED_AT_REQUEST")
    if not b_scopes:
        raise ValueError("ABSENCE_RECOVERY_ALLOWED_SCOPE_MISSING")
    old_checked = set(ref for s in b_scopes for ref in s.checked_refs)
    candidates = tuple(
        available[ref.span_id]
        for ref in b_scopes[0].members
        if ref.span_id in old_checked and ref.span_id in available
    )
    admission = AbsenceRun(b_scopes[0], candidates)
    b_admitted, b_reasons = admit_absence_catalog(admission, candidates, read_evidence, catalog)
    scopes = {
        s.scope_digest: _restore_scope(
            s,
            available,
            policy_ref,
            cutoff_at,
            b_admitted if s.kind == "ALLOWED_EXTRACTED_AT_REQUEST" else None,
            b_reasons if s.kind == "ALLOWED_EXTRACTED_AT_REQUEST" else (),
        )
        for s in observed.scope_sets
    }
    changed = any(key != scope.scope_digest for key, scope in scopes.items())
    items = tuple(_restore_item(i, scopes, observed) for i in observed.items)
    restored = AbsenceAssessments.model_validate(
        {
            **observed.model_dump(),
            "scope_sets": tuple({s.scope_digest: s for s in scopes.values()}.values()),
            "items": items,
            "not_checked_count": sum(
                i.a_result.outcome == "NOT_CHECKED" or i.b_result.outcome == "NOT_CHECKED"
                for i in items
            ),
            "reason_codes": tuple(
                dict.fromkeys(
                    (
                        *observed.reason_codes,
                        *b_reasons,
                        *((RECOVERY_SCOPE_CHANGED,) if changed else ()),
                    )
                )
            ),
        }
    )
    allowed = next(
        (s for s in restored.scope_sets if s.kind == "ALLOWED_EXTRACTED_AT_REQUEST"), None
    )
    actual = next(s for s in restored.scope_sets if s.stage_ref == restored.active_stage_ref)
    if allowed is None:
        raise ValueError("ABSENCE_RECOVERY_ALLOWED_SCOPE_MISSING")
    checked = {ref: available[ref] for s in restored.scope_sets for ref in s.checked_refs}
    work.absence = AbsenceRun(
        allowed_scope=allowed,
        allowed_spans=tuple(checked[ref] for ref in allowed.checked_refs),
        actual_spans=tuple(checked[ref] for ref in actual.checked_refs),
        actual_captured_at=actual.captured_at,
        checked_spans=checked,
        witness_attempted=any(i.injection_state != "NOT_NEEDED" for i in observed.items),
        catalog_usage_refs=admission.catalog_usage_refs,
    )
    work.consumed_sources.update(checked)
    payload = restored.model_dump(mode="json") if changed else raw
    work.result_only["absence_assessments"] = payload
    if not changed:
        # Preserve saved checks and delivery receipts exactly, including empty claims.
        return result
    if RECOVERY_SCOPE_CHANGED not in work.basis_reasons:
        work.basis_reasons.append(RECOVERY_SCOPE_CHANGED)
    updated = {**result, "absence_assessments": payload}
    active = tuple(i for i in items if i.origin_stage_ref == restored.active_stage_ref)
    warnings = sorted({i.warning_code for i in active if i.warning_code})
    if warnings:
        updated["absence_warning_codes"] = warnings
    else:
        updated.pop("absence_warning_codes", None)
    affected = {
        i.claim.statement.strip()
        for i in active
        if any(
            r.reason in {RECOVERY_SCOPE_CHANGED, ABSENCE_SCOPE_USAGE_LIMIT}
            for r in (i.a_result, i.b_result)
        )
    }
    findings = updated.get("confirmed_findings")
    if isinstance(findings, list):
        entries = cast(list[object], findings)
        retained: list[object] = [
            item
            for item in entries
            if not isinstance(item, dict)
            or cast(dict[str, object], item).get("statement") not in affected
        ]
        dropped = len(entries) - len(retained)
        if dropped:
            updated["confirmed_findings"] = retained
            prior = updated.get("confirmed_findings_dropped")
            updated["confirmed_findings_dropped"] = (
                prior if isinstance(prior, int) else 0
            ) + dropped
    return updated
