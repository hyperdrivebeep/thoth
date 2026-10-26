"""A witness uses the existing assembly once and never displaces the reviewer's evidence."""

from typing import cast

import pytest
from tests.integration.test_absence_result_payload import TEXT
from tests.unit.test_absence_claim_checks import assessed, proposal
from tests.unit.test_contextual_retrieval import Reader, context, work
from tests.unit.test_text_context_observation import bound, parse_text, spans

from thoth.application.services import research_context_assembler
from thoth.application.services.research_absence_delivery import (
    complete_absence_witness,
    prepare_absence_witness,
    with_absence_checks,
)
from thoth.application.services.research_absence_runtime import active_absence_items
from thoth.application.services.research_assembly import assemble_research_context
from thoth.domain.absence_claim import AbsenceAssessments, AbsenceRun
from thoth.domain.behavior_policy import RetrievalBehaviorPolicy
from thoth.domain.canonical import canonical_payload
from thoth.domain.enums import ModelRole
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.evidence_bundle import ContextAssembly
from thoth.domain.research_execution import ResearchBoundary, ResearchFence, ResearchWork
from thoth.ports.artifact_ledger import ArtifactLedgerPort


class Boundary:
    cancelled = False

    def check(self) -> None:
        if self.cancelled:
            raise ResearchFence("CANCELLED")


@pytest.mark.parametrize("limited", [False, True])
def test_witness_is_offered_once_and_rejected_if_original_input_cannot_fit(
    monkeypatch: pytest.MonkeyPatch,
    limited: bool,
) -> None:
    document = bound(parse_text(TEXT))
    all_spans = spans(document)
    actual = tuple(s for s in all_spans if s.locator.line is not None and s.locator.line < 8)
    checked = assessed(proposal(), actual, all_spans)
    assert checked.items[0].effective == "FOUND_OUTSIDE_MODEL_INPUT"
    current = work()
    current.boundary = cast(ResearchBoundary, Boundary())
    current.absence = AbsenceRun(
        checked.scope_sets[1], all_spans, checked_spans={s.span_id: s for s in all_spans}
    )
    current.result_only["absence_assessments"] = checked.model_dump(mode="json")
    original = context(actual)
    calls = 0

    def assemble(
        w: ResearchWork,
        ranking: tuple[str, ...],
        candidates: tuple[EvidenceSpan, ...],
        pool: tuple[EvidenceSpan, ...],
    ) -> ContextAssembly:
        nonlocal calls
        calls += 1
        return assemble_research_context(
            w, ranking, candidates, pool, cast(ArtifactLedgerPort, Reader(document)), lambda _: []
        )

    if limited:
        monkeypatch.setattr(
            research_context_assembler,
            "retrieval_policy",
            lambda: RetrievalBehaviorPolicy(max_spans=len(actual)),
        )
    received = prepare_absence_witness(original, current, assemble, lambda _: {})
    assert calls == 1
    assert set(actual) <= set(received.evidence)
    if limited:
        assert received is original
        item = active_absence_items(current)[0]
        assert item.injection_state == "NOT_DELIVERED"
        assert item.injection_reason == "ABSENCE_WITNESS_POLICY_LIMIT"
    else:
        assert set(checked.items[0].b_result.witness_refs) <= {s.span_id for s in received.evidence}
        assert active_absence_items(current)[0].injection_state == "PENDING"
        complete_absence_witness(current, received)
        assert active_absence_items(current)[0].injection_state == "DELIVERED"
    # A later review can find the same witness, but cannot add a second offer.
    current.result_only["absence_assessments"] = checked.model_dump(mode="json")
    again = prepare_absence_witness(original, current, assemble, lambda _: {})
    assert again is original and calls == 1
    assert active_absence_items(current)[0].injection_state == "ALREADY_ATTEMPTED"


def test_absence_view_preserves_numeric_view_and_budget_omission_is_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from thoth.application.services import research_absence_delivery

    data = spans(bound(parse_text(TEXT)))
    checked = assessed(proposal(), data, data)
    current = work()
    current.result_only["absence_assessments"] = checked.model_dump(mode="json")
    numeric: dict[str, object] = {"schema_version": "1.0.0", "contract": "numeric", "intervals": []}
    original = context(data).model_copy(
        update={"research_context": {"checked_observations": numeric}}
    )
    before = canonical_payload(original)
    projected = with_absence_checks(original, ModelRole.REVIEW_ADJUDICATOR, current)
    assert "absence_checks" in projected.research_context
    assert projected.research_context["checked_observations"] == numeric
    assert canonical_payload(original) == before
    monkeypatch.setattr(
        research_absence_delivery,
        "retrieval_policy",
        lambda: RetrievalBehaviorPolicy(character_budget=1),
    )
    refused = with_absence_checks(original, ModelRole.REVIEW_ADJUDICATOR, current)
    assert refused is original
    saved = AbsenceAssessments.model_validate(current.result_only["absence_assessments"])
    assert "ABSENCE_VIEW_POLICY_LIMIT" in saved.reason_codes
    assert saved.items == checked.items


def test_cancelled_witness_offer_cannot_claim_delivery() -> None:
    data = spans(bound(parse_text(TEXT)))
    actual = tuple(s for s in data if "Polars" not in s.exact_text)
    checked = assessed(proposal(), actual, data)
    current = work()
    boundary = Boundary()
    boundary.cancelled = True
    current.boundary = cast(ResearchBoundary, boundary)
    current.absence = AbsenceRun(checked.scope_sets[1], data)
    current.result_only["absence_assessments"] = checked.model_dump(mode="json")

    def forbidden(*_args: object) -> ContextAssembly:
        pytest.fail("cancelled request must not assemble a witness")

    with pytest.raises(ResearchFence, match="CANCELLED"):
        prepare_absence_witness(context(actual), current, forbidden, lambda _: {})
    assert active_absence_items(current)[0].injection_state == "PENDING"
