"""Literal scope checks never infer a language or certify global absence."""

from datetime import UTC, datetime
from typing import Literal

import pytest
from tests.unit.test_contextual_retrieval import work
from tests.unit.test_text_context_observation import bound, parse_text, spans

from thoth.application.services.research_absence_checks import (
    assess_absence_claims,
    literal_present,
    make_absence_scope,
)
from thoth.domain.absence_claim import (
    AbsenceAssessments,
    AbsenceClaim,
    AbsenceScope,
    ReviewProposalE2,
)
from thoth.domain.canonical import canonical_payload
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.evidence_requirements import ReviewProposal
from thoth.domain.research_reference import RevisionRef
from thoth.domain.research_stage import OUTPUT_CODECS

NOW = datetime(2026, 10, 27, tzinfo=UTC)
TEXT = "# Benchmark\n\nPolars 1.44.1\n\nMeasurements use KB and version 1.20.\n"


def source(text: str = TEXT) -> tuple[EvidenceSpan, ...]:
    return spans(bound(parse_text(text)))


def stage() -> RevisionRef:
    return work().request_ref.model_copy(update={"entity_id": "stage:1", "revision_id": "stage:1"})


def scope(
    values: tuple[EvidenceSpan, ...],
    *,
    actual: bool,
    current: tuple[EvidenceSpan, ...] | None = None,
    reasons: tuple[str, ...] = (),
) -> AbsenceScope:
    return make_absence_scope(
        work().request_ref,
        "ACTUAL_ROLE_INPUT" if actual else "ALLOWED_EXTRACTED_AT_REQUEST",
        values,
        captured_at=NOW,
        phase="TEST_INPUT" if actual else "TEST_START",
        policy_ref="policy",
        cutoff_at=NOW,
        policy_digest="a" * 64,
        stage_ref=stage() if actual else None,
        current=current,
        reasons=reasons,
    )


def proposal(
    target: str = "Polars 1.44.1",
    *,
    basis: Literal["SOURCE_LITERAL", "TRANSLATED", "CONCEPT"] = "SOURCE_LITERAL",
    kind: Literal["LITERAL", "IDENTIFIER", "CONCEPT"] = "LITERAL",
) -> ReviewProposalE2:
    statement = f"The checked documents do not contain {target}."
    return ReviewProposalE2(
        candidates=(),
        answer=statement,
        absence_claims=(
            AbsenceClaim(
                statement=statement,
                target=target,
                target_language_basis=basis,
                kind=kind,
                claimed_scope="ALL_MATERIAL",
                origin="ANSWER",
            ),
        ),
    )


def assessed(
    proposed: ReviewProposalE2,
    a: tuple[EvidenceSpan, ...],
    b: tuple[EvidenceSpan, ...],
    *,
    a_reasons: tuple[str, ...] = (),
    current: tuple[EvidenceSpan, ...] | None = None,
) -> AbsenceAssessments:
    return assess_absence_claims(
        work().request_ref,
        proposed,
        stage(),
        scope(a, actual=True, reasons=a_reasons, current=current),
        a,
        scope(b, actual=False, current=current),
        b,
    )


def test_old_and_new_codecs_remain_distinct_and_old_bytes_are_unchanged() -> None:
    old = ReviewProposal(candidates=(), answer="Existing answer")
    raw = canonical_payload(old)
    assert OUTPUT_CODECS["ReviewProposal"] is ReviewProposal
    assert OUTPUT_CODECS["ReviewProposalE2"] is ReviewProposalE2
    assert ReviewProposalE2.model_validate(old.model_dump()).absence_claims == ()
    assert canonical_payload(ReviewProposal.model_validate_json(raw)) == raw
    assert "absence_claims" not in old.model_json_schema()["properties"]


def test_positive_and_outside_input_are_separate_without_mutating_snapshots() -> None:
    all_spans = source()
    both = assessed(proposal(), all_spans, all_spans)
    item = both.items[0]
    assert item.a_result.outcome == item.b_result.outcome == "FOUND"
    assert item.effective == "FOUND_IN_MODEL_INPUT" and item.injection_state == "NOT_NEEDED"
    reduced = tuple(s for s in all_spans if "Polars" not in s.exact_text)
    outside = assessed(proposal(), reduced, all_spans)
    assert outside.items[0].a_result.outcome == "NOT_FOUND_WITHIN_SCOPE"
    assert outside.items[0].b_result.outcome == "FOUND"
    assert outside.items[0].effective == "FOUND_OUTSIDE_MODEL_INPUT"
    assert outside.items[0].injection_state == "PENDING"
    before = canonical_payload(outside)
    reduced = all_spans
    assert canonical_payload(outside) == before


@pytest.mark.parametrize("basis", ["TRANSLATED", "CONCEPT"])
def test_only_declared_source_literals_are_checked(basis: Literal["TRANSLATED", "CONCEPT"]) -> None:
    result = assessed(proposal("반복 분포", basis=basis), source(), source())
    assert result.items[0].a_result.outcome == result.items[0].b_result.outcome == "UNSUPPORTED"
    assert result.items[0].claim.target == "반복 분포"
    assert "TARGET_NOT_SOURCE_LITERAL" in result.reason_codes


def test_no_language_guess_and_no_global_absence() -> None:
    for target in ("Missing literal", "반복 분포"):
        result = assessed(proposal(target), source(), source())
        assert result.items[0].effective == "NOT_FOUND_WITHIN_SCOPES"
        assert result.items[0].warning_code is None
        assert result.prose_check_coverage == "DECLARED_CLAIMS_ONLY"
        assert "AUDITED_COMPLETE" not in result.model_dump_json()


@pytest.mark.parametrize("target", ["gcc에서", "C:\\data\\보고서.pdf를"])
@pytest.mark.parametrize("kind", ["LITERAL", "IDENTIFIER"])
def test_identifier_suffix_is_unsupported_not_rewritten(
    target: str, kind: Literal["LITERAL", "IDENTIFIER"]
) -> None:
    result = assessed(proposal(target, kind=kind), source(), source())
    assert result.items[0].effective == "UNSUPPORTED"
    assert result.items[0].claim.target == target
    assert "ABSENCE_IDENTIFIER_SUFFIX_AMBIGUOUS" in result.reason_codes


@pytest.mark.parametrize(
    "target,text,found",
    [
        ("1.2", "version 1.20", False),
        ("1.2", "version 1.2.0", False),
        ("1.2", "version 1.2.", True),
        ("KB", "kB", False),
        ("KB", "KB/s", False),
        ("KB", "units KB.", True),
        ("gcc", "gcc에서", False),
        ("Polars 1.44.1", "Polars 1.44.1\n", True),
    ],
)
def test_literal_identifier_and_decimal_boundaries(target: str, text: str, found: bool) -> None:
    assert literal_present(target, text) is found


@pytest.mark.parametrize("reason", ["CANCELLED", "SCAN_FAILED", "SCAN_PARTIAL"])
def test_incomplete_scopes_do_not_produce_absence(reason: str) -> None:
    result = assessed(proposal("Missing"), source(), source(), a_reasons=(reason,))
    assert result.items[0].a_result.outcome == "NOT_CHECKED"
    assert result.items[0].effective == "NOT_CHECKED"
    assert result.not_checked_count == 1
    found = assessed(proposal(), (), source(), a_reasons=(reason,))
    assert found.items[0].effective == "FOUND_A_SCOPE_UNCHECKED"


def test_withdrawn_or_changed_source_cannot_become_a_missing_literal() -> None:
    data = source()
    current = tuple(s for s in data if "Polars" not in s.exact_text)
    result = assessed(proposal(), data, data, current=current)
    assert result.items[0].a_result.outcome == result.items[0].b_result.outcome == "NOT_CHECKED"
    assert result.not_checked_count == 1


def test_claim_and_character_caps_are_explicit_not_failed_parsing() -> None:
    base = proposal()
    many = base.model_copy(update={"absence_claims": base.absence_claims * 14})
    result = assessed(many, source(), source())
    assert all(i.a_result.outcome == "FOUND" for i in result.items[:12])
    assert all(i.a_result.outcome == "NOT_CHECKED" for i in result.items[12:])
    assert result.not_checked_count == 2
    over = assessed(proposal("x" * 801), source(), source())
    assert over.items[0].a_result.outcome == "NOT_CHECKED"
    assert "ABSENCE_CHARACTER_LIMIT" in over.reason_codes


def test_scope_hash_witness_and_effective_outcome_are_checked_on_read() -> None:
    result = assessed(proposal(), source(), source())
    for mutation, reason in (
        ("hash", "ABSENCE_SCOPE_DIGEST_MISMATCH"),
        ("witness", "ABSENCE_WITNESS_OUTSIDE_SCOPE"),
        ("effective", "ABSENCE_EFFECTIVE_OUTCOME_MISMATCH"),
    ):
        raw = result.model_dump(mode="json")
        if mutation == "hash":
            raw["scope_sets"][0]["scope_digest"] = "f" * 64
        elif mutation == "witness":
            raw["items"][0]["a_result"]["witness_refs"] = ["outside"]
        else:
            raw["items"][0]["effective"] = "NOT_FOUND_WITHIN_SCOPES"
        with pytest.raises(ValueError, match=reason):
            AbsenceAssessments.model_validate(raw)


def test_check_digest_rejects_a_different_allowed_span_as_a_fabricated_witness() -> None:
    result = assessed(proposal(), source(), source())
    raw = result.model_dump(mode="json")
    raw["items"][0]["a_result"]["witness_refs"] = [source()[0].span_id]
    with pytest.raises(ValueError, match="ABSENCE_CHECK_DIGEST_MISMATCH"):
        AbsenceAssessments.model_validate(raw)


def test_mismatched_scan_text_cannot_become_a_negative_result() -> None:
    data = source()
    tampered = tuple(s.model_copy(update={"exact_text": "changed"}) for s in data)
    result = assess_absence_claims(
        work().request_ref,
        proposal(),
        stage(),
        scope(data, actual=True),
        tampered,
        scope(data, actual=False),
        data,
    )
    assert result.items[0].a_result.outcome == "NOT_CHECKED"
    assert result.items[0].b_result.outcome == "FOUND"
    assert result.items[0].effective == "FOUND_A_SCOPE_UNCHECKED"


def test_allowed_pool_is_not_truncated_to_the_model_span_limit() -> None:
    data = source("# Catalog\n\n" + "\n".join(f"TOKEN_{i:03d}" for i in range(201)))
    result = assessed(proposal("TOKEN_200"), data[:2], data)
    assert len(result.scope_sets[1].members) > 160
    assert result.scope_sets[1].scan_complete
    assert result.items[0].effective == "FOUND_OUTSIDE_MODEL_INPUT"


def test_origin_must_match_the_saved_answer_or_finding() -> None:
    original = proposal()
    invalid = original.model_copy(update={"answer": "Unrelated answer."})
    result = assessed(invalid, source(), source())
    assert not result.items[0].origin_bound
    assert result.items[0].effective == "NOT_CHECKED"
    assert "ABSENCE_ORIGIN_UNBOUND" in result.reason_codes


def test_start_scope_is_frozen_and_empty_claims_are_not_a_global_check() -> None:
    from thoth.application.services.research_absence_runtime import initialize_absence
    from thoth.domain.project import Project

    current = work()
    project = Project(
        project_id=current.request_ref.project_id,
        name="Test",
        cutoff_at=NOW,
        overlay="test",
        policy_binding_ref="policy",
    )
    initialize_absence(current, project, source(), NOW)
    assert current.absence is not None
    before = canonical_payload(current.absence.allowed_scope)
    initialize_absence(current, project, source("# New\n\nNew material"), NOW)
    assert canonical_payload(current.absence.allowed_scope) == before
    result = assessed(
        ReviewProposalE2(candidates=(), answer="No declared claims."), source(), source()
    )
    assert not result.items and result.not_checked_count == 0
    assert result.prose_check_coverage == "DECLARED_CLAIMS_ONLY"


def test_answer_origin_also_blocks_an_exact_duplicate_absence_card() -> None:
    from thoth.application.services.research_absence_runtime import adopt_absence_checked_findings
    from thoth.domain.evidence_requirements import ConfirmedFinding

    original = proposal()
    original = original.model_copy(
        update={
            "confirmed_findings": (
                ConfirmedFinding(
                    statement=original.answer,
                    evidence_refs=(source()[0].span_id,),
                    kind="VALID_NEGATIVE_FINDING",
                ),
                ConfirmedFinding(
                    statement="Unrelated document fact.",
                    evidence_refs=(source()[0].span_id,),
                    kind="DOCUMENT_FACT",
                ),
            )
        }
    )
    result = assessed(original, source(), source())
    current = work()
    current.result_only["absence_assessments"] = result.model_dump(mode="json")
    adopt_absence_checked_findings(original, current, source())
    assert current.context["confirmed_findings_dropped"] == 1
    assert current.context["confirmed_findings"] == [
        {
            "statement": "Unrelated document fact.",
            "evidence_refs": [source()[0].span_id],
            "kind": "DOCUMENT_FACT",
            "requirement_id": None,
        }
    ]
