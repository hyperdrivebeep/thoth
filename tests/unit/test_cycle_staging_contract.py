"""Freeze generated IDs and clock use before moving cycle record preparation."""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import cast

import pytest

from thoth.application.reducers import SufficiencySignals
from thoth.application.workflows.thread_cycle import ThreadCycleCommand, ThreadCycleService
from thoth.domain.action import (
    ActionCandidate,
    ActionCompilationPolicy,
    ActionPlan,
    DecisionAnalysis,
)
from thoth.domain.actor import ActorRef
from thoth.domain.canonical import CanonicalizationError
from thoth.domain.enums import ActorKind, EntityType, ExecutionAuthority, Reversibility, RiskTier
from thoth.domain.memory import MemoryRecord
from thoth.domain.outcome import OutcomeRecord
from thoth.domain.revision import StagedRevision

NOW = datetime(2026, 10, 13, tzinfo=UTC)


class TraceIds:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.counter = 0

    def new(self, namespace: str) -> str:
        self.counter += 1
        identifier = f"{namespace}:{self.counter}"
        self.events.append("id:" + identifier)
        return identifier


class TraceClock:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def now(self) -> datetime:
        self.events.append("clock")
        return NOW


def subject() -> tuple[ThreadCycleService, ThreadCycleCommand, list[str]]:
    events: list[str] = []
    # Isolate value preparation; any new service/IO dependency fails instead of becoming a mock.
    service = object.__new__(ThreadCycleService)
    vars(service)["_ids"] = TraceIds(events)
    vars(service)["_clock"] = TraceClock(events)
    command = ThreadCycleCommand(
        case_id="case:test",
        project_id="p",
        thread_id="thread:test",
        object_id="object:test",
        problem="fixture",
        cutoff_at=NOW,
        criteria=(),
        evidence=(),
        sufficiency_signals=SufficiencySignals(),
        action_policy=ActionCompilationPolicy(minimum_tier_by_family={}),
        actor=ActorRef(actor_id="agent:fixture", kind=ActorKind.AGENT, role="fixture"),
        policy_version="policy:fixture",
        model_policy_ref="model:fixture",
    )
    return service, command, events


STAGE = cast(Callable[..., StagedRevision], vars(ThreadCycleService)["_stage"])
PREPARE = cast(Callable[..., tuple[MemoryRecord, ...]], vars(ThreadCycleService)["_prepare_memory"])
OUTCOME = cast(Callable[..., OutcomeRecord], vars(ThreadCycleService)["_reanalysis_outcome"])


@pytest.mark.parametrize("parent", (False, True))
def test_staged_revision_preserves_id_clock_order_and_parent_basis(parent: bool) -> None:
    service, command, events = subject()
    heads = {"THREAD:t": "b" * 64} if parent else {}
    content = {"schema_version": "1.0.0", "note": "fixture"}
    result = STAGE(service, command, EntityType.THREAD, "t", content, (), heads)
    assert events == ["id:snapshot:1", "id:revision:2", "clock"]
    assert (
        result.snapshot.snapshot_id == "snapshot:1" and result.revision.revision_id == "revision:2"
    )
    assert result.revision.snapshot_id == result.snapshot.snapshot_id
    assert result.revision.parent_revision_digests == (("b" * 64,) if parent else ())
    assert result.revision.created_at == NOW and result.revision.actor == command.actor
    assert result.revision.reason == "thread cycle thread:test"
    assert result.snapshot.content == content and content == {
        "schema_version": "1.0.0",
        "note": "fixture",
    }
    assert heads == ({"THREAD:t": "b" * 64} if parent else {})


def test_canonical_rejection_precedes_all_id_and_clock_consumption() -> None:
    service, command, events = subject()
    with pytest.raises(CanonicalizationError, match="binary float"):
        STAGE(service, command, EntityType.THREAD, "t", {"bad": 0.5}, (), {})
    assert events == []


def test_memory_filters_keep_id_consumption_and_bind_actual_owner_revision() -> None:
    service, command, events = subject()
    values: tuple[tuple[EntityType, str, dict[str, object]], ...] = (
        (EntityType.THREAD, "t", {"note": "not a memory"}),
        (EntityType.HYPOTHESIS, "h", {"statement": "fixture statement"}),
        (EntityType.HYPOTHESIS, "portfolio", {"portfolio_id": "portfolio", "hypotheses": []}),
    )
    staged = tuple(
        STAGE(service, command, entity, identifier, content, (), {})
        for entity, identifier, content in values
    )
    events.clear()
    records = PREPARE(service, "p", staged)
    assert events == ["id:memory:7", "id:memory:8", "id:memory:9"]
    assert len(records) == 1 and records[0].memory_id == "memory:8"
    assert records[0].owner_revision_ref == staged[1].revision.revision_digest
    assert records[0].source_ref == "HYPOTHESIS:h"


@pytest.mark.parametrize("frontier", (False, True))
def test_reanalysis_outcome_selects_existing_action_and_reads_clock_once(frontier: bool) -> None:
    service, command, events = subject()
    alternatives = tuple(
        ActionCandidate(
            action_id="action:" + name,
            object_id="object:test",
            hypothesis_ids=(),
            action_family=name,
            specification="fixture action",
            expected_information_value="fixture information",
            risk_tier=RiskTier.R0,
            execution_authority=ExecutionAuthority.AUTO_R0,
            reversibility=Reversibility.FULL,
            external_write=False,
            sandbox_required=False,
            missing_evidence=("controlled fixture",),
        )
        for name in ("a", "b")
    )
    plan = ActionPlan(
        plan_id="plan:test",
        object_id="object:test",
        alternatives=alternatives,
        decision_analysis=DecisionAnalysis(
            decision="fixture",
            criteria=(),
            evaluations=(),
            uncertainty="unknown",
            sensitivity="unknown",
        ),
        frontier=("action:b",) if frontier else (),
        plan_revision_digest="a" * 64,
    )
    result = OUTCOME(service, command, plan, "b" * 64)
    assert result.action_id == ("action:b" if frontier else "action:a")
    assert result.recorded_at == NOW and events == ["clock"]
    assert result.input_head_set_digest == "b" * 64
    assert result.observed_evidence_refs == () and result.hypothesis_updates == ()
