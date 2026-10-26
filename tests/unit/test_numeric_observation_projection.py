"""Interval model views preserve display arithmetic and source refs without full records."""

from typing import cast

import pytest
from tests.unit.test_contextual_retrieval import context, work
from tests.unit.test_numeric_series import numeric_text, observed
from tests.unit.test_text_context_observation import bound, parse_text, spans

from thoth.application.services import numeric_observation_view
from thoth.application.services.numeric_observation_view import with_numeric_observations
from thoth.domain.behavior_policy import RetrievalBehaviorPolicy
from thoth.domain.canonical import canonical_payload
from thoth.domain.derived_calculation import SOURCE_ARITHMETIC_CONTRACT, CheckedObservations
from thoth.domain.enums import ModelRole
from thoth.domain.model import ContextPack
from thoth.domain.research_execution import ResearchWork

OPERATIONS = ("ratio", "difference", "log_log_slope")


def prepared(text: str) -> tuple[CheckedObservations, ContextPack, ResearchWork]:
    observation = observed(text)
    current = work()
    current.result_only["checked_observations"] = observation.model_dump(mode="json")
    return observation, context(spans(bound(parse_text(text)))), current


def view_of(received: ContextPack) -> dict[str, object]:
    return cast(dict[str, object], received.research_context["checked_observations"])


def restored(view: dict[str, object]) -> list[tuple[object, ...]]:
    owners = cast(list[dict[str, object]], view["series"])
    result: list[tuple[object, ...]] = []
    for row in cast(list[dict[str, object]], view["intervals"]):
        owner = owners[cast(int, row["series_index"])]
        assert set(row) == {"series_index", "sizes", "operand_refs", *OPERATIONS}
        for operation in OPERATIONS:
            cell = row[operation]
            if isinstance(cell, str):
                display, status, reason = cell, "CALCULATED", None
            else:
                failed = cast(dict[str, str], cell)
                assert set(failed) == {"status", "reason"}
                display, status, reason = None, failed["status"], failed["reason"]
            result.append(
                (
                    owner["label"],
                    owner["context_label"],
                    owner["size_label"],
                    tuple(cast(list[str], row["sizes"])),
                    tuple(cast(list[str], row["operand_refs"])),
                    operation.upper(),
                    display,
                    status,
                    reason,
                    owner["unit"] if operation == "difference" else "ratio",
                )
            )
    return result


@pytest.mark.parametrize(
    "text",
    [
        numeric_text(),
        numeric_text("다른 제품").replace("fill_nan", "처리 시간"),
        "# Measures\n\nRun A\n\n```\nm 100 n 0s\nm 1000 n -1s\nm 10000 n 2s\n```\n",
        "# Measures\n\nRun A\n\n```\nm 100 n 1s\ncondition changed\nm 1000 n 2s\n```\n",
    ],
)
def test_every_display_value_failure_and_reference_round_trips(text: str) -> None:
    observation, original, current = prepared(text)
    before = canonical_payload(observation)
    received = with_numeric_observations(original, ModelRole.SEMANTIC_REVIEWER, current)
    view = view_of(received)
    owners = {s.binding_digest: s for s in observation.series}
    expected = [
        (
            owners[c.binding_digest].label,
            owners[c.binding_digest].context_label,
            owners[c.binding_digest].size_label,
            c.sizes,
            c.operand_refs,
            c.operation,
            c.display_value,
            c.status,
            c.reason,
            c.unit,
        )
        for c in observation.calculations
    ]
    assert restored(view) == expected
    assert view["projection_version"] == "interval-display:1.0.0"
    assert view["display_decimal_places"] == 6
    assert view["contract"] == SOURCE_ARITHMETIC_CONTRACT
    assert view["authority"] == "DERIVED_OBSERVATION_ONLY"
    assert view["assumptions"] == sorted({a for s in observation.series for a in s.assumptions})
    assert view["diagnostic_reasons"] == sorted({d.reason for d in observation.diagnostics})
    assert view["not_delivered"] == 0 and view["delivery_reasons"] == []
    assert received.evidence == original.evidence
    saved = CheckedObservations.model_validate(current.result_only["checked_observations"])
    assert saved.deliveries[0].delivered == len(observation.calculations)
    # Only the delivery receipt changes; precision, values, calculations and all digests stay exact.
    assert (
        canonical_payload(saved.model_copy(update={"deliveries": observation.deliveries})) == before
    )
    assert '"value":' not in canonical_payload(view).decode()
    assert '"display_value":' not in canonical_payload(view).decode()


def test_budget_admits_whole_intervals_but_counts_delivered_operations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observation, original, current = prepared(numeric_text())
    full = view_of(with_numeric_observations(original, ModelRole.SEMANTIC_REVIEWER, current))
    intervals = cast(list[dict[str, object]], full["intervals"])
    reserved = {
        **full,
        "intervals": intervals[:1],
        "not_delivered": len(observation.calculations),
        "delivery_reasons": ["NUMERIC_POLICY_LIMIT"],
    }
    first_size = len(canonical_payload(reserved).decode())
    header_size = len(canonical_payload({**reserved, "intervals": []}).decode())
    raw_chars = sum(len(s.exact_text) for s in original.evidence)
    for room, expected_intervals in ((header_size - 1, None), (first_size - 1, 0), (first_size, 1)):
        monkeypatch.setattr(
            numeric_observation_view,
            "retrieval_policy",
            lambda room=room: RetrievalBehaviorPolicy(character_budget=raw_chars + room),
        )
        received = with_numeric_observations(original, ModelRole.HYPOTHESIS_GENERATOR, current)
        saved = CheckedObservations.model_validate(current.result_only["checked_observations"])
        delivery = next(d for d in saved.deliveries if d.role == "HYPOTHESIS_GENERATOR")
        assert received.evidence == original.evidence
        assert delivery.delivered == (expected_intervals or 0) * 3
        assert delivery.not_delivered == 18 - delivery.delivered
        assert delivery.reasons == ("NUMERIC_POLICY_LIMIT",)
        if expected_intervals is None:
            assert "checked_observations" not in received.research_context
        else:
            view = view_of(received)
            assert len(cast(list[object], view["intervals"])) == expected_intervals
            assert len(canonical_payload(view).decode()) <= room
            assert view["not_delivered"] == delivery.not_delivered


def test_missing_source_omits_the_whole_bound_series_and_preserves_other_refs() -> None:
    observation, original, current = prepared(numeric_text())
    missing = observation.series[0].points[0].source.span_id
    partial = original.model_copy(
        update={"evidence": tuple(s for s in original.evidence if s.span_id != missing)}
    )
    received = with_numeric_observations(partial, ModelRole.HYPOTHESIS_GENERATOR, current)
    view = view_of(received)
    assert len(restored(view)) == 12
    assert view["not_delivered"] == 6
    assert view["delivery_reasons"] == ["NUMERIC_SOURCE_NOT_IN_INPUT"]
    assert all(missing not in cast(tuple[str, str], row[4]) for row in restored(view))
