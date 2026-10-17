"""Pre-extraction ordering and failure characterization for route admission."""

from collections.abc import Callable
from typing import cast
from unittest.mock import Mock

import pytest
from tests.unit.services.acquisition_coordinator_support import HASH, T0
from tests.unit.services.counter_wave_support import Harness

from thoth.domain.acquisition import SearchIntent
from thoth.domain.connectors import ConnectorErrorCode, ConnectorFailure
from thoth.domain.counterevidence import CounterSearchPlan
from thoth.domain.enums import AuthorityState, CutoffState
from thoth.domain.investigation import InvestigationAuditRecord, InvestigationRecord
from thoth.domain.policy import PolicyDenialBasis, PolicyDenialReceipt


def observing[**P, R](
    call: Callable[P, R],
    events: list[str],
    label: str,
    fault: Exception | None = None,
) -> Callable[P, R]:
    def invoke(*args: P.args, **kwargs: P.kwargs) -> R:
        events.append(label)
        if fault is not None:
            raise fault
        return call(*args, **kwargs)

    return invoke


class ObservedHarness(Harness):
    def instrument(self, patch: pytest.MonkeyPatch, fault: Exception | None) -> None:
        patch.setattr(
            self._challenger,
            "challenge",
            observing(self._challenger.challenge, self.events, "challenge"),
        )
        patch.setattr(
            self._reviewer,
            "preflight",
            observing(self._reviewer.preflight, self.events, "preflight", fault),
        )

        def save_intent(intent: SearchIntent) -> None:
            self.events.append("intent.saved")

        cast(Mock, self._traces.put_search_intent).side_effect = save_intent
        original = self._investigations.audit

        def audit(
            value: InvestigationRecord,
            event_type: str,
            payload: dict[str, object],
        ) -> InvestigationAuditRecord:
            result = original(value, event_type, payload)
            self.events.append("audit:" + event_type)
            return result

        patch.setattr(self._investigations, "audit", audit)

    def _intent(self, plan: CounterSearchPlan) -> SearchIntent:
        result = super()._intent(plan)
        self.events.append("intent.created")
        return result


@pytest.mark.parametrize(
    ("case", "terminal"),
    [
        ("depth", "ABSTAINED"),
        ("authority", "AUTHORITY_REQUIRED"),
        ("prohibited", "ABSTAINED"),
        ("temporal", "ABSTAINED"),
        ("independence", "ABSTAINED"),
        ("allowlist", "POLICY_BLOCKED"),
        ("failure_policy", "POLICY_BLOCKED"),
        ("failure_driver", "ABSTAINED"),
    ],
)
async def test_preflight_precedence_and_prior_records(
    case: str,
    terminal: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = ObservedHarness(("counter first",))
    h.instrument(monkeypatch, None)
    if not case.startswith("failure"):
        h.policy = h.policy.model_copy(update={"connector_allowlist": ()})
    route_updates: dict[str, dict[str, object]] = {
        "depth": {
            "depth": h.policy.counter_search_limits.max_depth + 1,
            "source_authority": AuthorityState.UNCLASSIFIED,
        },
        "authority": {"source_authority": AuthorityState.UNCLASSIFIED},
        "prohibited": {"context_tags": ("HIDDEN_HOLDOUT",)},
        "temporal": {"temporal_state": CutoffState.AFTER_CUTOFF},
        "independence": {"source_territory": "primary-reader"},
    }
    h.routes = tuple(r.model_copy(update=route_updates.get(case, {})) for r in h.routes)
    if case.startswith("failure"):
        h.fail_acquire_at = 1
        denial = (
            PolicyDenialReceipt(
                project_id="p",
                action_kind="CONNECTOR",
                denial_basis=PolicyDenialBasis.SCOPE_DENIED,
                denied_at=T0,
                receipt_digest=HASH,
            )
            if case == "failure_policy"
            else None
        )
        h.failure = ConnectorFailure(
            ConnectorErrorCode.DRIVER_ERROR, "fixture", policy_denial=denial
        )
    result = await h.run()
    assert result.projection["loop_terminal"] == terminal
    expected = [
        "audit:investigation/started",
        "challenge",
        "intent.created",
        "intent.saved",
        "audit:investigation/counterSearchPlanned",
    ]
    if case != "depth":
        expected.append("preflight")
    if case.startswith("failure"):
        expected.extend(["io.enter", "io.raise"])
    expected.extend(["audit:investigation/completed", "finalize"])
    assert h.events == expected
    assert h.committed == []


async def test_reviewer_exception_propagates_after_planning_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = ObservedHarness(("counter first",))
    fault = RuntimeError("reviewer failure")
    h.instrument(monkeypatch, fault)
    with pytest.raises(RuntimeError) as caught:
        await h.run()
    assert caught.value is fault
    assert h.events == [
        "audit:investigation/started",
        "challenge",
        "intent.created",
        "intent.saved",
        "audit:investigation/counterSearchPlanned",
        "preflight",
    ]
    assert h.requests == [] and h.finalized is None
