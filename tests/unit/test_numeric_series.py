"""Arithmetic acceptance uses printed numbers, never a scientific-answer oracle."""

from decimal import Decimal
from typing import cast

import pytest
from tests.unit.test_contextual_retrieval import Reader, context, work
from tests.unit.test_text_context_observation import bound, parse_text, spans

from thoth.adapters.document_calculators import (
    AdjacentSeriesCalculator,
    DocumentCalculatorRegistry,
    default_document_calculator,
)
from thoth.adapters.reference_calculators.registry import UnitRegistry, default_units
from thoth.application.services.document_calculation import DocumentCalculationService
from thoth.application.services.numeric_observation_view import (
    summarize_checked_observations,
    validate_numeric_publication,
    with_numeric_observations,
)
from thoth.domain.canonical import canonical_payload
from thoth.domain.derived_calculation import SOURCE_ARITHMETIC_CONTRACT, CheckedObservations
from thoth.domain.enums import ModelRole
from thoth.ports.artifact_ledger import ArtifactLedgerPort


def numeric_text(product: str = "Polars") -> str:
    return (
        "# Measurements\n\n"
        + "\n\n".join(
            f"{product} {version}\n\n```text\n"
            + "\n".join(
                f"fill_nan {size} cols {value}s"
                for size, value in zip((100, 1000, 10000), values, strict=True)
            )
            + "\n```"
            for version, values in [
                ("1.43.2", ("0.001", "0.005", "0.059")),
                ("1.44.1", ("0.002", "0.025", "1.557")),
                ("1.44.2", ("0.001", "0.025", "1.396")),
            ]
        )
        + "\n"
    )


def observed(
    text: str, *, artifact: str = "artifact:text", reverse: bool = False
) -> CheckedObservations:
    doc = bound(parse_text(text, artifact_id=artifact))
    source = spans(doc)
    result = DocumentCalculationService(default_document_calculator()).observe(
        work().request_ref,
        tuple(reversed(source)) if reverse else source,
        cast(ArtifactLedgerPort, Reader(doc)),
    )
    assert result is not None
    return result


@pytest.mark.parametrize("product", ["Polars", "다른 제품"])
def test_printed_series_ratios_slopes_and_product_independence(product: str) -> None:
    result = observed(numeric_text(product))
    assert len(result.series) == 3 and len(result.calculations) == 18 and not result.diagnostics
    by_binding = {s.binding_digest: s for s in result.series}

    def pick(version: str, operation: str):
        return next(
            c
            for c in result.calculations
            if version in by_binding[c.binding_digest].context_label
            and c.operation == operation
            and c.sizes == ("1000", "10000")
        )

    assert pick("1.44.1", "RATIO").value == "62.28"
    assert pick("1.44.1", "LOG_LOG_SLOPE").display_value == "1.794349"
    assert pick("1.43.2", "RATIO").value == "11.8"
    assert Decimal(pick("1.44.1", "DIFFERENCE").value or "") == Decimal("1.532")
    assert all(c.authority == "DERIVED_OBSERVATION_ONLY" for c in result.calculations)
    assert result.contract == SOURCE_ARITHMETIC_CONTRACT


def test_content_identity_is_independent_of_upload_ids_and_iteration_order() -> None:
    text = numeric_text()
    original = observed(text)
    reordered = observed(text, reverse=True)
    other = observed(text, artifact="artifact:new-upload")
    assert canonical_payload(original) == canonical_payload(reordered)
    assert [s.series_digest for s in original.series] == [s.series_digest for s in other.series]
    assert [s.points_digest for s in original.series] == [s.points_digest for s in other.series]
    assert all(
        a.binding_digest != b.binding_digest
        for a, b in zip(original.series, other.series, strict=True)
    )
    changed = observed(text.replace("1.557s", "1.558s"))
    assert original.series[1].series_digest != changed.series[1].series_digest
    assert original.series[1].points_digest != changed.series[1].points_digest


def test_units_manifest_is_order_independent_and_binds_calculations() -> None:
    left, right = UnitRegistry(), UnitRegistry()
    for name in ("s", "ms"):
        left.register(name, "time", Decimal("1") if name == "s" else Decimal(".001"))
    for name in ("ms", "s"):
        right.register(name, "time", Decimal("1") if name == "s" else Decimal(".001"))
    assert left.manifest() == right.manifest()
    assert (
        AdjacentSeriesCalculator(left).unit_registry_digest
        == AdjacentSeriesCalculator(right).unit_registry_digest
    )
    assert default_units().convert(Decimal("1000"), "ms", "s") == Decimal("1")
    with pytest.raises(ValueError, match="DOCUMENT_CALCULATOR_UNAVAILABLE"):
        DocumentCalculatorRegistry((default_document_calculator(),)).resolve("unsupported")
    units = default_units()
    calc = AdjacentSeriesCalculator(units)
    series = observed(numeric_text()).series[0]
    units.register("extra", "new", Decimal(1))
    with pytest.raises(ValueError, match="NUMERIC_UNIT_BASIS_CHANGED"):
        calc.calculate(series)


@pytest.mark.parametrize(
    "body,reason",
    [
        ("m 100 n 1s\nm 100 n 2s", "NUMERIC_DUPLICATE_SIZE"),
        ("m 100 n 1s\nm 1000 n 2ms", "NUMERIC_MIXED_UNITS"),
        ("m 100 n 1watts\nm 1000 n 2watts", "NUMERIC_UNIT_UNSUPPORTED"),
        ("m 100 n NaNs\nm 1000 n 2s", "NUMERIC_LITERAL_UNSUPPORTED"),
        ("m 100 n 1e9999s\nm 1000 n 2s", "NUMERIC_LITERAL_LIMIT"),
        ("m 0 n 1s\nm 1000 n 2s", "NUMERIC_SIZE_NONPOSITIVE"),
        ("m 100 n 1s\ncondition changed\nm 1000 n 2s", "NUMERIC_BLOCK_AMBIGUOUS"),
        ("m 100 n 1s\nm unknown n 2s\nm 10000 n 3s", "NUMERIC_LITERAL_UNSUPPORTED"),
    ],
)
def test_ambiguous_or_unsupported_groups_are_not_calculated(body: str, reason: str) -> None:
    result = observed("# Measurements\n\nRun A\n\n```\n" + body + "\n```\n")
    assert not result.calculations and not result.series
    assert reason in {d.reason for d in result.diagnostics}


def test_grouping_does_not_merge_operations_or_condition_blocks() -> None:
    text = (
        "# Measurements\n\nRun A\n\n```\nm 100 n 1s\nfirst m 100 n 2s\n"
        "m 1000 n 3s\nfirst m 1000 n 8s\n```\n\nRun B\n\n```\n"
        "m 100 n 10s\nm 1000 n 20s\n```\n"
    )
    result = observed(text)
    assert sorted((s.label, len(s.points)) for s in result.series) == [
        ("first m", 2),
        ("m", 2),
        ("m", 2),
    ]
    assert sorted(
        Decimal(c.value or "") for c in result.calculations if c.operation == "RATIO"
    ) == [Decimal(2), Decimal(3), Decimal(4)]


def test_zero_and_negative_values_do_not_fabricate_slopes() -> None:
    result = observed(
        "# Measurements\n\nRun A\n\n```\nm 100 n 0s\nm 1000 n -1s\nm 10000 n 2s\n```\n"
    )
    slopes = [c for c in result.calculations if c.operation == "LOG_LOG_SLOPE"]
    assert all(c.status == "NOT_CALCULATED" and c.value is None for c in slopes)
    assert result.calculations[0].reason == "ZERO_DENOMINATOR"
    assert result.calculations[1].value == "-1"


@pytest.mark.parametrize("values", [("1e1024", "1e-1024"), ("1e-1024", "1e1024")])
def test_arithmetic_range_failure_is_not_a_zero_or_infinite_result(
    values: tuple[str, str],
) -> None:
    result = observed(
        f"# Measurements\n\nRun A\n\n```\nm 100 n {values[0]}s\nm 1000 n {values[1]}s\n```\n"
    )
    for calculation in result.calculations:
        if calculation.operation in {"RATIO", "LOG_LOG_SLOPE"}:
            assert calculation.status == "NOT_CALCULATED"
            assert calculation.reason == "ARITHMETIC_OR_UNIT_INVALID"
            assert calculation.value is None


@pytest.mark.parametrize("value,display", [("1.0000005", "1.000000"), ("1.0000015", "1.000002")])
def test_six_decimal_display_is_half_even(value: str, display: str) -> None:
    result = observed(f"# Measurements\n\nRun A\n\n```\nm 100 n 0s\nm 1000 n {value}s\n```\n")
    difference = next(c for c in result.calculations if c.operation == "DIFFERENCE")
    assert difference.value == value and difference.display_value == display


def test_numeric_view_uses_remaining_budget_without_dropping_raw_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from thoth.application.services import numeric_observation_view
    from thoth.domain.behavior_policy import RetrievalBehaviorPolicy

    doc = bound(parse_text(numeric_text()))
    source = spans(doc)
    w = work()
    w.result_only["checked_observations"] = observed(numeric_text()).model_dump(mode="json")
    monkeypatch.setattr(
        numeric_observation_view,
        "retrieval_policy",
        lambda: RetrievalBehaviorPolicy(character_budget=1),
    )
    received = with_numeric_observations(context(source), ModelRole.HYPOTHESIS_GENERATOR, w)
    assert received.evidence == source and "checked_observations" not in received.research_context
    stored = CheckedObservations.model_validate(w.result_only["checked_observations"])
    assert stored.deliveries[0].not_delivered == 18
    assert "NUMERIC_POLICY_LIMIT" in stored.deliveries[0].reasons


def test_missing_members_and_unclosed_boundaries_are_explicit() -> None:
    text = "# Measurements\n\nRun A\n\n```\nm 100 n 1s\nm 1000 n 2s\n```\n"
    doc = bound(parse_text(text))
    source = spans(doc)
    partial = tuple(s for s in source if "1000" not in s.exact_text)
    result = DocumentCalculationService(default_document_calculator()).observe(
        work().request_ref, partial, cast(ArtifactLedgerPort, Reader(doc))
    )
    assert result is not None and not result.calculations
    assert result.diagnostics[0].reason == "NUMERIC_CONTEXT_INCOMPLETE"
    unclosed = observed(text.removesuffix("```\n"))
    assert not unclosed.calculations
    assert unclosed.diagnostics[0].reason in {
        "NUMERIC_CONTEXT_UNCERTAIN",
        "NUMERIC_BOUNDARY_UNCONFIRMED",
    }


def test_source_corruption_and_foreign_request_fail_closed() -> None:
    doc = bound(parse_text(numeric_text()))
    source = spans(doc)
    bad = (source[0].model_copy(update={"exact_text": "tampered"}), *source[1:])
    service = DocumentCalculationService(default_document_calculator())
    reader = cast(ArtifactLedgerPort, Reader(doc))
    with pytest.raises(ValueError, match="NUMERIC_SOURCE_BASIS_MISMATCH"):
        service.observe(work().request_ref, bad, reader)
    with pytest.raises(ValueError, match="NUMERIC_PROJECT_MISMATCH"):
        service.observe(
            work().request_ref.model_copy(update={"project_id": "other"}), source, reader
        )


def test_role_delivery_and_summary_preserve_provenance_without_mutating_evidence() -> None:
    doc = bound(parse_text(numeric_text()))
    source = spans(doc)
    w = work()
    data = observed(numeric_text())
    w.result_only["checked_observations"] = data.model_dump(mode="json")
    before = canonical_payload(context(source))
    for role in (
        ModelRole.SEMANTIC_REVIEWER,
        ModelRole.REVIEW_ADJUDICATOR,
        ModelRole.HYPOTHESIS_GENERATOR,
    ):
        received = with_numeric_observations(context(source), role, w)
        assert received.evidence == source
        assert (
            cast(dict[str, object], received.research_context["checked_observations"])["contract"]
            == SOURCE_ARITHMETIC_CONTRACT
        )
    assert canonical_payload(context(source)) == before
    assert (
        with_numeric_observations(context(source), ModelRole.ACTION_PLANNER, w).research_context
        == {}
    )
    received = with_numeric_observations(context(()), ModelRole.HYPOTHESIS_GENERATOR, w)
    assert (
        cast(dict[str, object], received.research_context["checked_observations"])["not_delivered"]
        == 18
    )
    validate_numeric_publication(
        {"checked_observations": data.model_dump(mode="json")}, w.request_ref, source
    )
    with pytest.raises(ValueError, match="NUMERIC_PUBLICATION_SOURCE_MISMATCH"):
        validate_numeric_publication(
            {"checked_observations": data.model_dump(mode="json")}, w.request_ref, ()
        )
    summary = summarize_checked_observations(data.model_dump(mode="json"))
    assert len(cast(list[object], summary["display_rows"])) == 18
    assert "series" not in summary
    corrupt = data.model_dump(mode="json")
    corrupt["calculations"][0]["value"] = "99"
    with pytest.raises(ValueError, match="NUMERIC_OUTPUT_DIGEST_MISMATCH"):
        CheckedObservations.model_validate(corrupt)


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("operation", "DIFFERENCE", "NUMERIC_INPUT_DIGEST_MISMATCH"),
        ("calculator_version", "other:1", "NUMERIC_INPUT_DIGEST_MISMATCH"),
        ("unit", "s", "NUMERIC_OUTPUT_UNIT_MISMATCH"),
    ],
)
def test_saved_calculation_keeps_its_operation_version_and_unit_binding(
    field: str, value: str, reason: str
) -> None:
    raw = observed(numeric_text()).model_dump(mode="json")
    raw["calculations"][0][field] = value
    with pytest.raises(ValueError, match=reason):
        CheckedObservations.model_validate(raw)


def test_source_refs_cannot_omit_an_operand_before_publication() -> None:
    raw = observed(numeric_text()).model_dump(mode="json")
    raw["series"][0]["source_refs"] = []
    with pytest.raises(ValueError, match="NUMERIC_POINT_SOURCE_REF_MISSING"):
        CheckedObservations.model_validate(raw)


def test_korean_operation_and_size_labels_remain_exact() -> None:
    result = observed(numeric_text().replace("fill_nan", "처리 시간").replace("cols", "열"))
    assert {(s.label, s.size_label) for s in result.series} == {("처리 시간", "열")}
    assert any(c.value == "62.28" for c in result.calculations)


def test_new_request_binds_records_without_changing_same_source_model_projection() -> None:
    doc = bound(parse_text(numeric_text()))
    source = spans(doc)
    first, second = work(), work()
    second.request_ref = second.request_ref.model_copy(update={"revision_id": "revision:2"})
    service = DocumentCalculationService(default_document_calculator())
    views: list[bytes] = []
    bindings: list[str] = []
    for current in (first, second):
        data = service.observe(current.request_ref, source, cast(ArtifactLedgerPort, Reader(doc)))
        assert data is not None
        bindings.append(data.series[0].binding_digest)
        current.result_only["checked_observations"] = data.model_dump(mode="json")
        received = with_numeric_observations(context(source), ModelRole.SEMANTIC_REVIEWER, current)
        views.append(canonical_payload(received))
    assert bindings[0] != bindings[1] and views[0] == views[1]


def test_registered_calculator_version_changes_content_basis_without_core_branches() -> None:
    class OtherCalculator(AdjacentSeriesCalculator):
        version = "other-adjacent-series:1.0.0"

    other = OtherCalculator(default_units())
    registry = DocumentCalculatorRegistry((default_document_calculator(), other))
    doc = bound(parse_text(numeric_text()))
    result = DocumentCalculationService(registry.resolve(other.version)).observe(
        work().request_ref, spans(doc), cast(ArtifactLedgerPort, Reader(doc))
    )
    assert result is not None
    assert result.series[0].series_digest != observed(numeric_text()).series[0].series_digest
    assert all(c.calculator_version == other.version for c in result.calculations)
    assert any(c.value == "62.28" for c in result.calculations)
