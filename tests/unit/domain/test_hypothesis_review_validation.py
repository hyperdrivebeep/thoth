from dataclasses import dataclass
from typing import cast

from thoth.adapters.models.reference_schema import constrain_hypothesis_review
from thoth.domain.hypothesis_review_validation import review_coverage, target_ids
from thoth.domain.research_failure import failure_cause
from thoth.ports.model import ModelOutputContractHold


@dataclass(frozen=True)
class _Item:
    hypothesis_id: str


@dataclass(frozen=True)
class _Portfolio:
    hypotheses: tuple[_Item, ...]


def _portfolio(*ids: str) -> _Portfolio:
    return _Portfolio(tuple(_Item(item) for item in ids))


def _decisions(*ids: str) -> tuple[_Item, ...]:
    return tuple(_Item(item) for item in ids)


def _record(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return cast(dict[str, object], value)


def test_four_hyp_two_decisions_is_not_ok() -> None:
    coverage = review_coverage(
        _portfolio("hyp:a", "hyp:b", "hyp:c", "hyp:d"),
        _decisions("hyp:a", "hyp:b"),
    )
    assert coverage.ok is False
    assert coverage.missing == ("hyp:c", "hyp:d")
    assert coverage.duplicate == ()
    assert coverage.unexpected == ()


def test_empty_portfolio_empty_review_is_ok() -> None:
    coverage = review_coverage(_portfolio(), ())
    assert coverage.ok is True
    assert target_ids(_portfolio()) == ()


def test_single_hypothesis_exact_review_is_ok() -> None:
    coverage = review_coverage(_portfolio("hyp:a"), _decisions("hyp:a"))
    assert coverage.ok is True
    assert coverage.received_ids == ("hyp:a",)


def test_duplicate_decision_is_not_ok() -> None:
    coverage = review_coverage(_portfolio("hyp:a", "hyp:b"), _decisions("hyp:a", "hyp:a"))
    assert coverage.ok is False
    assert coverage.duplicate == ("hyp:a",)


def test_unexpected_decision_is_not_ok() -> None:
    coverage = review_coverage(_portfolio("hyp:a"), _decisions("hyp:a", "hyp:other"))
    assert coverage.ok is False
    assert coverage.unexpected == ("hyp:other",)


def test_coverage_hold_reason_is_typed() -> None:
    cause = failure_cause(
        ModelOutputContractHold("HYPOTHESIS_REVIEW_COVERAGE_MISMATCH"),
        "RESEARCH_EXECUTION",
    )
    assert cause.reason_code == "HYPOTHESIS_REVIEW_COVERAGE_MISMATCH"


def test_schema_pins_ids_and_length() -> None:
    schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "decisions": {
                "type": "array",
                "items": {"type": "object", "properties": {"hypothesis_id": {"type": "string"}}},
            }
        },
    }
    out = constrain_hypothesis_review(schema, ("hyp:a", "hyp:b"))
    decisions = _record(_record(out["properties"])["decisions"])
    assert decisions["minItems"] == 2
    assert decisions["maxItems"] == 2
    item = _record(decisions["items"])
    hypothesis_id = _record(_record(item["properties"])["hypothesis_id"])
    assert hypothesis_id["enum"] == ["hyp:a", "hyp:b"]
