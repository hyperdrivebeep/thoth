"""Learning-only recovery restores evidence bindings, not a new investigation or witness call."""

from datetime import timedelta

import pytest
from tests.unit.test_absence_claim_checks import NOW, assessed, proposal, source
from tests.unit.test_contextual_retrieval import work

from thoth.application.services.research_absence_recovery import (
    RECOVERY_SCOPE_CHANGED,
    restore_absence_recovery,
)
from thoth.domain.absence_claim import AbsenceAssessments, ReviewProposalE2, absence_check_digest
from thoth.domain.canonical import canonical_payload
from thoth.domain.enums import AuthorityState


@pytest.mark.parametrize("empty", [False, True])
def test_restore_saved_scope_and_checks_exactly_including_empty_claims(empty: bool) -> None:
    data = source()
    candidate = ReviewProposalE2(candidates=(), answer="Saved answer") if empty else proposal()
    observed = assessed(candidate, data, data)
    saved: dict[str, object] = {
        "answer": candidate.answer,
        "absence_assessments": observed.model_dump(mode="json"),
    }
    before = canonical_payload(saved)
    current = work()
    assert current.absence is None
    result = restore_absence_recovery(current, saved, data, policy_ref="policy", cutoff_at=NOW)
    assert result is saved and canonical_payload(result) == before
    assert current.result_only["absence_assessments"] == saved["absence_assessments"]
    assert current.absence is not None
    assert current.absence.allowed_scope == observed.scope_sets[1]
    assert current.absence.allowed_spans == data and current.absence.actual_spans == data
    assert current.absence.checked_spans == {s.span_id: s for s in data}
    assert current.consumed_sources == current.absence.checked_spans
    assert not current.basis_reasons


@pytest.mark.parametrize(
    "injection", ["PENDING", "DELIVERED", "NOT_DELIVERED", "ALREADY_ATTEMPTED"]
)
def test_original_witness_receipt_is_retained_without_reoffering(injection: str) -> None:
    data = source()
    observed = assessed(proposal(), data, data)
    raw = observed.model_dump(mode="json")
    raw["items"][0]["injection_state"] = injection
    saved: dict[str, object] = {"absence_assessments": raw}
    current = work()
    extra = data[0].model_copy(
        update={
            "span_id": "new:span",
            "artifact_id": "new:artifact",
            "source_version_id": "new:version",
        }
    )
    result = restore_absence_recovery(
        current, saved, (*data, extra), policy_ref="policy", cutoff_at=NOW
    )
    assert result is saved
    assert current.absence is not None and current.absence.witness_attempted
    assert current.absence.allowed_scope == observed.scope_sets[1]
    assert extra.span_id not in current.consumed_sources
    assert (
        AbsenceAssessments.model_validate(result["absence_assessments"]).items[0].injection_state
        == injection
    )


@pytest.mark.parametrize("change", ["removed", "version", "text", "authority", "policy", "cutoff"])
def test_changed_scope_becomes_not_checked_without_changing_saved_record(change: str) -> None:
    data = source()
    observed = assessed(proposal(), data, data)
    saved: dict[str, object] = {
        "answer": "Original answer",
        "absence_assessments": observed.model_dump(mode="json"),
    }
    before = canonical_payload(saved)
    target = next(s for s in data if "Polars" in s.exact_text)
    available = data
    if change == "removed":
        available = tuple(s for s in data if s is not target)
    elif change in {"version", "text", "authority"}:
        updates: dict[str, dict[str, object]] = {
            "version": {"source_version_id": "changed:version"},
            "text": {"exact_text": "Corrupt text with the unchanged stored hash"},
            "authority": {"authority_state": AuthorityState.NOT_ADMISSIBLE},
        }
        update = updates[change]
        available = tuple(s.model_copy(update=update) if s is target else s for s in data)
    current = work()
    result = restore_absence_recovery(
        current,
        saved,
        available,
        policy_ref="changed-policy" if change == "policy" else "policy",
        cutoff_at=NOW + timedelta(days=1) if change == "cutoff" else NOW,
    )
    restored = AbsenceAssessments.model_validate(result["absence_assessments"])
    assert restored.items[0].a_result.outcome == restored.items[0].b_result.outcome == "NOT_CHECKED"
    assert restored.items[0].effective == "NOT_CHECKED" and restored.not_checked_count == 1
    assert restored.items[0].claim == observed.items[0].claim
    assert restored.items[0].origin_stage_ref == observed.items[0].origin_stage_ref
    assert restored.scope_sets[1].members == observed.scope_sets[1].members
    assert RECOVERY_SCOPE_CHANGED in restored.reason_codes
    assert RECOVERY_SCOPE_CHANGED in current.basis_reasons
    assert current.absence is not None
    assert target.span_id not in current.absence.checked_spans
    assert canonical_payload(saved) == before and result["answer"] == saved["answer"]


def test_only_changed_b_scope_is_invalidated_and_pending_witness_is_not_sent() -> None:
    data = source()
    actual = tuple(s for s in data if "Polars" not in s.exact_text)
    observed = assessed(proposal(), actual, data)
    assert observed.items[0].injection_state == "PENDING"
    current = work()
    saved: dict[str, object] = {"absence_assessments": observed.model_dump(mode="json")}
    result = restore_absence_recovery(current, saved, actual, policy_ref="policy", cutoff_at=NOW)
    restored = AbsenceAssessments.model_validate(result["absence_assessments"])
    assert restored.items[0].a_result == observed.items[0].a_result
    assert restored.items[0].b_result.outcome == "NOT_CHECKED"
    assert restored.items[0].effective == "NOT_CHECKED"
    assert restored.items[0].injection_state == "NOT_DELIVERED"
    assert current.absence is not None and current.absence.witness_attempted


def test_old_checker_version_survives_invalidation_and_only_affected_card_is_removed() -> None:
    data = source()
    candidate = proposal("MissingLiteral")
    observed = assessed(candidate, data, data)
    old = observed.model_dump(mode="json")
    old["checker_version"] = "literal-absence:1.0.0"
    for item in old["items"]:
        item["check_digest"] = absence_check_digest(item, checker_version="literal-absence:1.0.0")
    saved: dict[str, object] = {
        "absence_assessments": old,
        "answer": candidate.answer,
        "confirmed_findings": [
            {"statement": candidate.answer, "kind": "VALID_NEGATIVE_FINDING"},
            {"statement": "Unrelated document fact", "kind": "DOCUMENT_FACT"},
        ],
        "confirmed_findings_dropped": 2,
    }
    before = canonical_payload(saved)
    result = restore_absence_recovery(work(), saved, (), policy_ref="policy", cutoff_at=NOW)
    restored = AbsenceAssessments.model_validate(result["absence_assessments"])
    assert restored.checker_version == "literal-absence:1.0.0"
    assert restored.items[0].effective == "NOT_CHECKED"
    assert result["confirmed_findings"] == [
        {"statement": "Unrelated document fact", "kind": "DOCUMENT_FACT"}
    ]
    assert result["confirmed_findings_dropped"] == 3
    assert result["answer"] == candidate.answer and canonical_payload(saved) == before


def test_legacy_absence_missing_is_not_backfilled_and_foreign_request_stays_rejected() -> None:
    current = work()
    legacy: dict[str, object] = {"answer": "Saved legacy answer"}
    assert (
        restore_absence_recovery(current, legacy, (), policy_ref="policy", cutoff_at=NOW) is legacy
    )
    assert current.absence is None and not current.result_only
    observed = assessed(proposal(), source(), source())
    current.request_ref = current.request_ref.model_copy(update={"revision_id": "foreign"})
    with pytest.raises(ValueError, match="ABSENCE_RECOVERY_REQUEST_MISMATCH"):
        restore_absence_recovery(
            current,
            {"absence_assessments": observed.model_dump(mode="json")},
            source(),
            policy_ref="policy",
            cutoff_at=NOW,
        )
    assert current.absence is None and not current.consumed_sources
