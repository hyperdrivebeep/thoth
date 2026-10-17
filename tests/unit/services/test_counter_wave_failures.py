"""Failures after acquisition preserve original partial effects and propagation."""

from collections.abc import Callable
from typing import cast

import pytest
from pydantic import JsonValue
from tests.unit.services.counter_wave_support import Harness

from thoth.application.services.critical_counter_search_finalization import (
    IndependentConfirmationTracker,
)
from thoth.application.services.investigation_service import InvestigationService
from thoth.domain.counterevidence import CounterReviewTerminal, CounterSearchPlan
from thoth.domain.evidence_graph import EvidenceSourceRecord
from thoth.domain.investigation import InvestigationRecord


def fail_at[**P, R](
    call: Callable[P, R],
    name: str,
    target: str,
    events: list[str],
    fault: RuntimeError,
) -> Callable[P, R]:
    def invoke(*args: P.args, **kwargs: P.kwargs) -> R:
        events.append(name)
        if name == target:
            raise fault
        return call(*args, **kwargs)

    return invoke


@pytest.mark.parametrize("point", ["record_wave", "pause", "resume", "missing_digest"])
@pytest.mark.parametrize("relevant", [True, False])
async def test_wave_write_failures_preserve_prior_effects(
    point: str,
    relevant: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = Harness(("counter first" if relevant else "unrelated first",))
    fault = RuntimeError("controlled wave write failure")
    for name in ("record_wave", "pause", "resume"):
        method = getattr(InvestigationService, name)
        monkeypatch.setattr(
            InvestigationService, name, fail_at(method, name, point, h.events, fault)
        )
    if point == "missing_digest":
        original = InvestigationService.pause

        def pause(self: InvestigationService, value: InvestigationRecord) -> InvestigationRecord:
            return original(self, value).model_copy(update={"checkpoint_digest": None})

        monkeypatch.setattr(InvestigationService, "pause", pause)
    error = ValueError if point == "missing_digest" else RuntimeError
    with pytest.raises(error) as caught:
        await h.run()
    if point == "missing_digest":
        assert str(caught.value) == "counter-search checkpoint digest is missing"
    else:
        assert caught.value is fault
    expected = ["io.enter", "io.return"]
    expected.extend(["stage", "commit"] if relevant else ["detach:binding:0"])
    expected.append("record_wave")
    if point != "record_wave":
        expected.append("pause")
    if point == "resume":
        expected.append("resume")
    assert h.events == expected
    assert len(h.committed) == int(relevant)
    assert h.detached == ([] if relevant else ["binding:0"])
    assert h.finalized is None


async def test_confirmation_failure_is_inside_commit_absorption_and_keeps_prior_mutations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = Harness(("counter first", "counter second"))
    original = IndependentConfirmationTracker.record
    seen: list[IndependentConfirmationTracker] = []

    def record(
        self: IndependentConfirmationTracker,
        plan: CounterSearchPlan,
        source: EvidenceSourceRecord,
        terminal: CounterReviewTerminal,
    ) -> None:
        seen.append(self)
        original(self, plan, source, terminal)
        if len(seen) == 1:
            raise RuntimeError("after confirmation mutation")

    monkeypatch.setattr(IndependentConfirmationTracker, "record", record)
    result = await h.run()
    assert len(seen) == 2 and seen[0] is seen[1]
    assert seen[0].counter_groups == {"group:0", "group:1"}
    assert seen[0].seen_lineages == {"lineage:0", "lineage:1"}
    assert len(h.committed) == 2 and h.detached == ["binding:0"]
    waves = cast(list[dict[str, JsonValue]], result.projection["waves"])
    assert [w["computed_voi"] for w in waves] == ["0", "0.45"]
    assert [w["decision_rank_after"] for w in waves] == [-1, -2]
    assert [w["sufficiency_after"] for w in waves] == ["confirmations:1", "confirmations:2"]
    assert result.projection["loop_terminal"] == "ELIMINATED_WITHIN_SCOPE"
    gate = cast(dict[str, JsonValue], result.projection["gate"])
    assert gate["accepted_evidence_refs"] == ["span:0", "span:1"]
