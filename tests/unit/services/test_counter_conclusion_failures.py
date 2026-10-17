"""Conclusion failures stop before budget sampling and final publication."""

import pytest
from tests.unit.services.counter_wave_support import Harness
from tests.unit.services.test_counter_wave_failures import fail_at

from thoth.application.services.counter_search_usage import CounterSearchUsage
from thoth.application.services.critical_counter_search import IndependentGateReviewer
from thoth.application.services.investigation_service import InvestigationService


@pytest.mark.parametrize("point", ["aggregate", "stop"])
async def test_conclusion_failure_propagates_without_snapshot_or_finalization(
    point: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = Harness(("counter first", "counter second"))
    fault = RuntimeError("controlled conclusion failure")
    monkeypatch.setattr(
        IndependentGateReviewer,
        "aggregate",
        fail_at(IndependentGateReviewer.aggregate, "aggregate", point, h.events, fault),
    )
    monkeypatch.setattr(
        InvestigationService,
        "stop",
        fail_at(InvestigationService.stop, "stop", point, h.events, fault),
    )
    monkeypatch.setattr(
        CounterSearchUsage,
        "snapshot",
        fail_at(CounterSearchUsage.snapshot, "snapshot", point, h.events, fault),
    )
    with pytest.raises(RuntimeError) as caught:
        await h.run()
    assert caught.value is fault
    expected = ["io.enter", "io.return", "stage", "commit"] * 2 + ["aggregate"]
    if point == "stop":
        expected.append("stop")
    assert h.events == expected
    assert "snapshot" not in h.events and "finalize" not in h.events
    assert len(h.committed) == 2 and h.finalized is None
