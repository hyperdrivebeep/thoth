"""Caller-owned wave accumulators and late publication lookup stay observable."""

from collections.abc import Callable
from typing import cast

import pytest
from tests.unit.services.counter_wave_support import Harness

from thoth.application.services.connector_service import ConnectorAcquisition
from thoth.application.services.counter_wave_evaluation import (
    CounterWaveEvaluator,
    CounterWaveOutcome,
)
from thoth.application.services.critical_counter_search_finalization import (
    IndependentConfirmationTracker,
)
from thoth.domain.counterevidence import IndependentGateReview


@pytest.mark.parametrize("side", ["counter", "supports"])
async def test_root_owned_accumulators_keep_identity_and_exact_increments(
    side: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = Harness((f"{side} first", f"{side} second"))
    identities: list[tuple[object, ...]] = []

    def observe[**P](call: Callable[P, CounterWaveOutcome]) -> Callable[P, CounterWaveOutcome]:
        def invoke(*args: P.args, **kwargs: P.kwargs) -> CounterWaveOutcome:
            seen = cast(set[str], kwargs["seen_content_digests"])
            tracker = cast(IndependentConfirmationTracker, kwargs["confirmations"])
            counter = cast(list[str], kwargs["counter_refs"])
            support = cast(list[str], kwargs["support_refs"])
            tracks = cast(list[str], kwargs["executed_track_ids"])
            gate = cast(IndependentGateReview, kwargs["gate"])
            acquisition = cast(ConnectorAcquisition, kwargs["acquisition"])
            before_seen, before_tracks = set(seen), list(tracks)
            before_counter, before_support = list(counter), list(support)
            identities.append((seen, tracker, counter, support, tracks))
            result = call(*args, **kwargs)
            assert seen == before_seen | {acquisition.ingestion.object_digest}
            assert tracks == before_tracks + list(gate.executed_track_ids)
            assert counter == before_counter + (
                list(gate.accepted_evidence_refs) if side == "counter" else []
            )
            assert support == before_support + (
                list(gate.accepted_evidence_refs) if side == "supports" else []
            )
            assert tracker.maximum == len(identities)
            assert tracker.seen_groups == {f"group:{i}" for i in range(len(identities))}
            assert tracker.seen_lineages == {f"lineage:{i}" for i in range(len(identities))}
            return result

        return invoke

    monkeypatch.setattr(CounterWaveEvaluator, "evaluate", observe(CounterWaveEvaluator.evaluate))
    await h.run()
    assert len(identities) == 2
    assert all(first is second for first, second in zip(identities[0], identities[1], strict=True))


class LateLookupHarness(Harness):
    def observe_late_commit(self, patch: pytest.MonkeyPatch) -> None:
        def observe[**P](call: Callable[P, None]) -> Callable[P, None]:
            def invoke(*args: P.args, **kwargs: P.kwargs) -> None:
                self.events.append("late.commit.lookup")
                call(*args, **kwargs)

            return invoke

        patch.setattr(self, "_commit_evidence_candidate", observe(self._commit_evidence_candidate))


async def test_commit_method_is_resolved_when_called(monkeypatch: pytest.MonkeyPatch) -> None:
    h = LateLookupHarness(("counter first", "counter second"))
    h.observe_late_commit(monkeypatch)
    await h.run()
    assert h.events.count("late.commit.lookup") == len(h.committed) == 2
