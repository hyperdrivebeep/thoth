"""Frozen examples test grounding without changing any product generation or storage path."""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError
from scripts.hypothesis_grounding_replay import (
    FIXTURES,
    Example,
    Proposal,
    ReplayContext,
    ReplaySuite,
    main,
    score_row,
)

from thoth.domain.hypothesis import ExpectedResult

SUITE = ReplaySuite.model_validate_json((FIXTURES / "cases.json").read_text(encoding="utf-8"))
BY_ID = {example.case_id: example for example in SUITE.examples}
BASELINE = json.loads((FIXTURES / "baseline.json").read_text(encoding="utf-8"))
ROWS = [row for test in BASELINE["payload"]["tests"] for row in test["rows"]]
BASELINE_SHA = "6fffc576d483e4c64ad5ed1b4cd9ea51add280f4b38ccef985405faeeca9ea3b"
CASES_SHA = "2bc6b8bb2ccf1c33e506790eb3cec7fe4e0209a93bbca8476409975b4857d80e"


def test_frozen_data_retains_original_rows_and_exact_negative_categories() -> None:
    payload = BASELINE["payload"]
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    assert hashlib.sha256(encoded).hexdigest() == BASELINE["payload_sha256"] == BASELINE_SHA
    assert BASELINE["source_sha256"] == (
        "59d3042a31b1059bfc1f5a239742a2606f606e29f908bf78c6c551ecd5d11868"
    )
    assert len(payload["hypotheses"]) == 4 and len(payload["tests"]) == 5 and len(ROWS) == 20
    assert all(row["basis"] == [] for row in ROWS)
    assert all(ExpectedResult.model_validate(row).model_dump(mode="json") == row for row in ROWS)
    assert payload["table_fill"]["state"] == "SKIPPED_FULL"
    assert payload["table_fill"]["open_before"] == payload["table_fill"]["open_after"] == 0
    assert payload["dropped_expected_results"] == []
    assert hashlib.sha256((FIXTURES / "cases.json").read_bytes()).hexdigest() == CASES_SHA
    assert {case.case_id for case in SUITE.examples if case.category == "NEGATIVE"} == {
        "number-only",
        "condition-mismatch",
        "unit-mismatch",
        "denominator-mismatch",
        "stale-pinned-revision",
        "foreign-or-private-scope",
        "invented-span",
        "past-observation-to-future-effect",
        "counterevidence-as-support",
        "synthetic-as-measured",
    }


@pytest.mark.parametrize("case", SUITE.examples, ids=lambda case: case.case_id)
def test_safe_output_passes_and_adversarial_output_fails_without_mutation(case: Example) -> None:
    before = copy.deepcopy(case.model_dump(mode="json"))
    good = score_row(case.context, case.accepted)
    bad = score_row(case.context, case.rejected)
    assert good.passed and good.reasons == ()
    assert good.false_verification_promotions == good.unauthorized_links == 0
    assert not bad.passed and bad.reasons
    assert case.model_dump(mode="json") == before
    assert score_row(case.context, case.accepted) == good


@pytest.mark.parametrize("index", range(20))
def test_legacy_twenty_rows_remain_unassessed_without_fabricating_basis(index: int) -> None:
    row = ROWS[index]
    before = json.dumps(row, ensure_ascii=False, sort_keys=True)
    context = ReplayContext(
        kind="LEGACY",
        project_id="project:baseline",
        cutoff_at=datetime(2026, 10, 23, tzinfo=UTC),
        data_origin="SYNTHETIC",
        legacy_knowledge_state="PREDICTED",
        legacy_expected=row["expected"],
    )
    proposal = Proposal(
        role="UNASSESSED",
        knowledge_state="PREDICTED",
        expected=row["expected"],
        data_origin="SYNTHETIC",
    )
    assert score_row(context, proposal).passed
    assert json.dumps(row, ensure_ascii=False, sort_keys=True) == before
    inferred = proposal.model_copy(update={"role": "HYPOTHESIS_ONLY", "rationale": "guess"})
    assert not score_row(context, inferred).passed  # no retroactive role inference
    assert not score_row(context, proposal.model_copy(update={"expected": "changed"})).passed


@pytest.mark.parametrize(
    "name",
    [
        "number-only",
        "condition-mismatch",
        "unit-mismatch",
        "denominator-mismatch",
        "stale-pinned-revision",
        "counterevidence-as-support",
    ],
)
def test_corrected_evidence_can_pass_without_changing_the_case_label(name: str) -> None:
    bad = BY_ID[name]
    actual = BY_ID["exact-rain-observation"]
    context = bad.context.model_copy(
        update={
            "evidence": actual.context.evidence,
            "pinned_sources": actual.context.pinned_sources,
        }
    )
    proposal = bad.rejected.model_copy(
        update={
            "role": "OBSERVATION_RESTATEMENT",
            "basis": actual.accepted.basis,
            "observation": actual.accepted.observation,
        }
    )
    assert score_row(context, proposal).passed


def test_a_positive_restatement_cannot_pass_by_omitting_or_changing_the_observation() -> None:
    case = BY_ID["exact-rain-observation"]
    result = score_row(case.context, case.rejected)
    assert result.missing_observation_links == 1
    assert "MISSING_REQUIRED_OBSERVATION_LINK" in result.reasons
    assert not score_row(
        case.context, case.accepted.model_copy(update={"observation": None})
    ).passed
    assert case.context.target is not None
    changed = case.context.target.model_copy(update={"numerator": 15})
    assert not score_row(
        case.context, case.accepted.model_copy(update={"observation": changed})
    ).passed


def test_same_project_private_and_foreign_project_are_independent_guards() -> None:
    case = BY_ID["exact-rain-observation"]
    private = case.context.model_copy(update={"permitted_span_ids": ()})
    assert score_row(private, case.accepted).unauthorized_links == 1
    foreign = case.context.evidence[0].model_copy(update={"project_id": "project:elsewhere"})
    result = score_row(case.context.model_copy(update={"evidence": (foreign,)}), case.accepted)
    assert result.unauthorized_links == 1 and "FOREIGN_PROJECT_LINK" in result.reasons


def test_cutoff_and_source_bytes_are_checked_without_reading_current_heads() -> None:
    case = BY_ID["exact-rain-observation"]
    earlier = case.context.model_copy(
        update={"cutoff_at": case.context.cutoff_at.replace(year=2025)}
    )
    assert "AFTER_CUTOFF" in score_row(earlier, case.accepted).reasons
    altered = case.context.evidence[0].model_copy(update={"exact_text": "different bytes"})
    result = score_row(case.context.model_copy(update={"evidence": (altered,)}), case.accepted)
    assert "TEXT_HASH_MISMATCH" in result.reasons
    # The v1 source stays valid when it is the pinned historical basis; no latest-head I/O.
    assert score_row(case.context, case.accepted).passed


def test_legacy_links_are_preserved_without_becoming_verified_support() -> None:
    case = BY_ID["exact-rain-observation"]
    reference = case.context.evidence[0].model_copy(update={"relation": "REFERENCE_ONLY"})
    context = case.context.model_copy(
        update={
            "kind": "LEGACY",
            "target": None,
            "legacy_basis": case.accepted.basis,
            "legacy_knowledge_state": case.accepted.knowledge_state,
            "legacy_expected": case.accepted.expected,
            "evidence": (reference,),
        }
    )
    proposal = case.accepted.model_copy(update={"role": "UNASSESSED", "observation": None})
    assert score_row(context, proposal).passed
    assert not score_row(context, proposal.model_copy(update={"basis": ()})).passed
    assert not score_row(context, proposal.model_copy(update={"verification_claim": True})).passed


def test_future_unknown_is_distinct_from_a_known_but_unconfirmed_prediction() -> None:
    case = BY_ID["past-observation-to-future-effect"]
    assert score_row(case.context, case.accepted).passed
    unknown = case.accepted.model_copy(
        update={
            "role": "UNASSESSED",
            "knowledge_state": "UNKNOWN",
            "expected": None,
        }
    )
    assert score_row(case.context, unknown).passed
    with pytest.raises(ValidationError):
        Proposal.model_validate({**case.accepted.model_dump(), "expected": None})


def test_valid_evidence_cannot_grant_verification_or_duplicate_its_link() -> None:
    case = BY_ID["exact-rain-observation"]
    promoted = score_row(
        case.context, case.accepted.model_copy(update={"verification_claim": True})
    )
    assert promoted.false_verification_promotions == 1 and not promoted.passed
    duplicated = case.accepted.model_copy(update={"basis": case.accepted.basis * 2})
    assert "DUPLICATE_LINK" in score_row(case.context, duplicated).reasons


def test_grade_covers_every_case_and_returns_failure_on_a_fabricated_link(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "predictions.json"
    values = {case.case_id: case.accepted.model_dump(mode="json") for case in SUITE.examples}
    path.write_text(json.dumps(values), encoding="utf-8")
    assert main(["grade", "--predictions", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["passed"] is True
    values["invented-span"] = BY_ID["invented-span"].rejected.model_dump(mode="json")
    path.write_text(json.dumps(values), encoding="utf-8")
    assert main(["grade", "--predictions", str(path)]) == 1
    assert json.loads(capsys.readouterr().out)["passed"] is False
    del values["exact-rain-observation"]
    path.write_text(json.dumps(values), encoding="utf-8")
    with pytest.raises(SystemExit) as error:
        main(["grade", "--predictions", str(path)])
    assert error.value.code == 2


@pytest.mark.parametrize("corrupt", ["unknown_state", "missing_role", "duplicate_key"])
def test_invalid_proposals_fail_as_input_errors(tmp_path: Path, corrupt: str) -> None:
    values = {case.case_id: case.accepted.model_dump(mode="json") for case in SUITE.examples}
    if corrupt == "unknown_state":
        values["exact-rain-observation"]["knowledge_state"] = "UNKNOWN"
    elif corrupt == "missing_role":
        del values["exact-rain-observation"]["role"]
    raw = json.dumps(values)
    if corrupt == "duplicate_key":
        raw = raw[:-1] + ', "exact-rain-observation": {}}'
    path = tmp_path / "bad.json"
    path.write_text(raw, encoding="utf-8")
    with pytest.raises(SystemExit) as error:
        main(["grade", "--predictions", str(path)])
    assert error.value.code == 2


def test_an_empty_case_suite_cannot_pass(tmp_path: Path) -> None:
    path = tmp_path / "empty.json"
    path.write_text(
        '{"schema_version":"grounding_replay_cases.v1","examples":[]}', encoding="utf-8"
    )
    with pytest.raises(SystemExit) as error:
        main(["check-fixtures", "--cases", str(path)])
    assert error.value.code == 2
