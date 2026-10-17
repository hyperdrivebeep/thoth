"""Direct checks of the Action draft helpers: same inputs, same values, ids and clock order (90)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast

import pytest

from thoth.application.services.action_effect_policy import effect_impact
from thoth.application.services.action_record_drafts import (
    creation_draft,
    creation_expectation,
    plan_composition_draft,
    portfolio_composition_draft,
    portfolio_evaluation_updates,
    portfolio_revision_draft,
)
from thoth.application.services.action_revision_drafts import action_revision_records
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.enums import ActorKind, EntityType

BASE = datetime(2026, 9, 1, tzinfo=UTC)


class Calls:
    def __init__(self) -> None:
        self.log: list[tuple[str, str]] = []
        self.ticks = 0

    def new(self, prefix: str) -> str:
        self.log.append(("ids", prefix))
        return f"{prefix}:{len(self.log)}"

    def now(self) -> datetime:
        self.log.append(("clock", ""))
        self.ticks += 1
        return BASE + timedelta(seconds=self.ticks)


def specification(**changes: object) -> dict[str, object]:
    base: dict[str, object] = {
        "description": "direct",
        "expected_observation_or_change": {"description": "observation"},
        "stop_conditions": ("stop",),
        "observability": "receipt",
    }
    base.update(changes)
    return base


@pytest.mark.parametrize(
    ("changes", "effect", "mapping", "missing"),
    [
        ({}, {"effect_completeness_confirmed": True}, {"description": "observation"}, ()),
        (
            {"expected_observation_or_change": None},
            {"effect_completeness_confirmed": True},
            {"description": "UNRESOLVED", "status": "MISSING"},
            (),
        ),
        (
            {"expected_observation_or_change": {}},
            {"effect_completeness_confirmed": True},
            {},
            ("expected_observation_or_change",),
        ),
        ({}, {}, {"description": "observation"}, ("effect_completeness_confirmed",)),
        (
            {"stop_conditions": (), "observability": ""},
            {"effect_completeness_confirmed": True},
            {"description": "observation"},
            ("stop_conditions", "observability"),
        ),
    ],
)
def test_expectation_maps_the_expectation_and_lists_missing_fields_in_order(
    changes: dict[str, object],
    effect: dict[str, object],
    mapping: dict[str, object],
    missing: tuple[str, ...],
) -> None:
    given = specification(**changes)
    before = dict(given)
    assert creation_expectation(given, effect) == (mapping, missing)
    assert given == before


@pytest.mark.parametrize(
    ("tier", "state"),
    [
        ("R0", "NOT_REQUIRED"),
        ("R1", "NOT_REQUIRED"),
        ("R2", "NOT_REQUIRED"),
        ("R3", "NOT_PREPARED"),
        ("R4", "NOT_REQUIRED"),
    ],
)
def test_creation_draft_orders_calls_and_keeps_the_given_objects(tier: str, state: str) -> None:
    calls = Calls()
    given = specification()
    effect: dict[str, object] = {"effect_completeness_confirmed": True}
    refs = ("hypothesis:1",)
    tests = ()
    secondary = ("RISK_REDUCTION",)
    evidence = ("span:1",)
    processes = ("PROCESS",)
    roles = ("role",)
    expected: dict[str, object] = {"description": "observation"}
    draft = creation_draft(
        project_id="project:d",
        object_id="object:d",
        portfolio_id="portfolio:d",
        hypothesis_refs=refs,
        test_refs=tests,
        primary_purpose="ANALYSIS_COMPUTATION",
        secondary_purposes=secondary,
        specification=given,
        evidence_refs=evidence,
        effect=effect,
        risk_tier=tier,
        policy_state="POLICY",
        processes=processes,
        roles=roles,
        expected_mapping=expected,
        missing=(),
        ids=calls,
        clock=calls,
    )
    assert calls.log == [("ids", "action-revision"), ("ids", "action"), ("clock", "")]
    assert list(draft) == [
        "action_revision_id",
        "action_id",
        "project_id",
        "object_id",
        "portfolio_id",
        "hypothesis_refs",
        "test_refs",
        "primary_purpose",
        "secondary_purposes",
        "specification",
        "evidence_refs",
        "expected_observation_or_change",
        "effect_vector",
        "impact_set",
        "risk_tier",
        "required_processes",
        "required_roles",
        "proposal_state",
        "decision_state",
        "policy_state",
        "authorization_state",
        "freshness",
        "created_at",
    ]
    assert (draft["action_revision_id"], draft["action_id"]) == ("action-revision:1", "action:2")
    assert draft["created_at"] == BASE + timedelta(seconds=1)
    assert draft["effect_vector"] is effect and draft["hypothesis_refs"] is refs
    assert draft["test_refs"] is tests and draft["secondary_purposes"] is secondary
    assert draft["evidence_refs"] is evidence and draft["required_processes"] is processes
    assert draft["required_roles"] is roles and draft["expected_observation_or_change"] is expected
    assert draft["specification"] == {**given, "missing_fields": ()}
    assert draft["specification"] is not given and "missing_fields" not in given
    assert draft["impact_set"] == effect_impact(effect)
    assert draft["authorization_state"] == state
    assert draft["proposal_state"] == "CANDIDATE" and draft["decision_state"] == "NOT_EVALUATED"
    assert draft["freshness"] == "CURRENT" and draft["policy_state"] == "POLICY"


def test_creation_draft_marks_a_draft_when_fields_are_missing() -> None:
    calls = Calls()
    draft = creation_draft(
        project_id="p",
        object_id="o",
        portfolio_id="f",
        hypothesis_refs=(),
        test_refs=(),
        primary_purpose="SIMULATION",
        secondary_purposes=(),
        specification={},
        evidence_refs=(),
        effect={},
        risk_tier="R3",
        policy_state="POLICY_UNDEFINED",
        processes=(),
        roles=(),
        expected_mapping={},
        missing=("stop_conditions",),
        ids=calls,
        clock=calls,
    )
    assert draft["proposal_state"] == "DRAFT"
    assert draft["specification"] == {"missing_fields": ("stop_conditions",)}


def composition(
    current: Any, criteria: tuple[dict[str, object], ...], calls: Calls
) -> dict[str, object]:
    return portfolio_composition_draft(
        project_id="p",
        object_id="o",
        action_ids=("action:1", "action:2"),
        decision_need="need",
        criteria_proposal=criteria,
        effective_id="portfolio:x",
        current=current,
        ids=calls,
        clock=calls,
    )


def test_portfolio_composition_defaults_split_and_supersession() -> None:
    calls = Calls()
    draft = composition(None, (), calls)
    assert calls.log == [("ids", "action-portfolio-revision"), ("clock", "")]
    assert draft["mandatory_criteria"] == (
        {
            "criterion_id": "mandatory:policy",
            "name": "policy admissibility",
            "mandatory": True,
            "rationale": "prohibited actions cannot enter the feasible set",
        },
    )
    assert draft["enhancing_criteria"] == () and draft["supersedes_revision_digest"] is None
    assert (draft["portfolio_id"], draft["action_refs"]) == (
        "portfolio:x",
        ("action:1", "action:2"),
    )
    assert list(draft)[-3:] == ["decision_state", "supersedes_revision_digest", "created_at"]
    criteria: tuple[dict[str, object], ...] = (
        {"criterion_id": "m", "mandatory": True},
        {"criterion_id": "e"},
        {"criterion_id": "f", "mandatory": False},
    )
    split = composition(SimpleNamespace(revision_digest="digest"), criteria, Calls())
    assert split["mandatory_criteria"] == (criteria[0],)
    assert split["enhancing_criteria"] == (criteria[1], criteria[2])
    assert split["mandatory_criteria"][0] is criteria[0]  # pyright: ignore[reportIndexIssue]
    assert split["supersedes_revision_digest"] == "digest"


def action(policy: str, tier: str, name: str) -> Any:
    return SimpleNamespace(
        action_id=name,
        policy_state=policy,
        risk_tier=tier,
        impact_set={"data": name},
        expected_observation_or_change={"description": name},
    )


@pytest.mark.parametrize(
    ("policies", "preferences", "state", "sensitivity"),
    [
        (("OK", "OK"), (), "PREFERENCE_REQUIRED", "RANK_UNSTABLE_WITHOUT_PREFERENCES"),
        (("OK", "OK"), ({"w": 1},), "RECOMMENDATION_READY", "STABLE_WITH_SUPPLIED_PREFERENCES"),
        (("OK", "PROHIBITED"), (), "RECOMMENDATION_READY", "STABLE_WITH_SUPPLIED_PREFERENCES"),
        (("PROHIBITED", "PROHIBITED"), (), "EVALUATED", "STABLE_WITH_SUPPLIED_PREFERENCES"),
        ((), (), "EVALUATED", "STABLE_WITH_SUPPLIED_PREFERENCES"),
    ],
)
def test_evaluation_updates_state_gates_and_shared_values(
    policies: tuple[str, ...],
    preferences: tuple[dict[str, object], ...],
    state: str,
    sensitivity: str,
) -> None:
    actions = tuple(
        action(policy, "R0", f"action:{index}") for index, policy in enumerate(policies)
    )
    updates = portfolio_evaluation_updates(
        actions, evaluation_method="method", preference_inputs=preferences
    )
    assert list(updates) == [
        "evaluation_method",
        "scenario_results",
        "uncertainty",
        "sensitivity",
        "value_of_information",
        "decision_state",
        "recommendation_ref",
    ]
    assert updates["decision_state"] == state and updates["sensitivity"] == sensitivity
    assert updates["recommendation_ref"] is None and updates["evaluation_method"] == "method"
    results = cast("tuple[dict[str, Any], ...]", updates["scenario_results"])
    assert len(results) == len(actions)
    for result, source in zip(results, actions, strict=True):
        assert result["impact"] is source.impact_set
        assert result["expected"] is source.expected_observation_or_change
        assert result["mandatory_gate"] == (
            "FAIL" if source.policy_state == "PROHIBITED" else "PASS"
        )


def test_revision_draft_never_changes_updates_and_orders_ids_before_clock() -> None:
    calls = Calls()

    def dump(mode: str) -> dict[str, object]:
        return {"keep": 1, "revision_digest": "old", "decision_state": "A"}

    current = SimpleNamespace(revision_digest="old", model_dump=dump)
    updates: dict[str, object] = {"decision_state": "B", "supersedes_revision_digest": "ignored"}
    before = dict(updates)
    draft = portfolio_revision_draft(current, updates=updates, ids=calls, clock=calls)  # pyright: ignore[reportArgumentType]
    assert updates == before
    assert calls.log == [("ids", "action-portfolio-revision"), ("clock", "")]
    assert draft == {
        "keep": 1,
        "decision_state": "B",
        "supersedes_revision_digest": "old",
        "portfolio_revision_id": "action-portfolio-revision:1",
        "created_at": BASE + timedelta(seconds=1),
    }


def test_plan_composition_merges_derived_values_and_keeps_given_objects() -> None:
    calls = Calls()
    steps: tuple[dict[str, object], ...] = ({"step_id": "s"},)
    edges: tuple[dict[str, str], ...] = ()
    derived: dict[str, object] = {"cumulative_impact": {"a": 1}, "required_role_union": ("r",)}
    current = SimpleNamespace(authorization_refs=("authorization:1",), revision_digest="old")
    draft = plan_composition_draft(
        project_id="p",
        object_id="o",
        selected_action_refs=("action:1",),
        steps=steps,
        dependency_edges=edges,
        effective_id="plan:x",
        derived=derived,
        current=current,  # pyright: ignore[reportArgumentType]
        ids=calls,
        clock=calls,
    )
    assert calls.log == [("ids", "action-plan-revision"), ("clock", "")]
    assert draft["steps"] is steps and draft["dependency_edges"] is edges
    assert draft["cumulative_impact"] is derived["cumulative_impact"]
    assert draft["authorization_refs"] == ("authorization:1",)
    assert draft["supersedes_revision_digest"] == "old" and draft["plan_id"] == "plan:x"
    assert (draft["validation_state"], draft["freshness"]) == ("VALIDATED", "CURRENT")
    assert list(draft)[:7] == [
        "plan_revision_id",
        "plan_id",
        "project_id",
        "object_id",
        "selected_action_refs",
        "steps",
        "dependency_edges",
    ]
    fresh = plan_composition_draft(
        project_id="p",
        object_id="o",
        selected_action_refs=(),
        steps=steps,
        dependency_edges=edges,
        effective_id="plan:x",
        derived={},
        current=None,
        ids=Calls(),
        clock=Calls(),
    )
    assert fresh["authorization_refs"] == () and fresh["supersedes_revision_digest"] is None


@pytest.mark.parametrize("supersedes", [None, "a" * 64])
def test_revision_records_use_one_snapshot_id_and_one_actor_object(supersedes: str | None) -> None:
    calls = Calls()
    content: dict[str, object] = {"field": "value"}

    def dump(mode: str) -> dict[str, object]:
        return content

    record = SimpleNamespace(project_id="project:r", created_at=BASE, model_dump=dump)
    snapshot, actor, revision = action_revision_records(
        record,  # pyright: ignore[reportArgumentType]
        entity_id="entity:r",
        revision_id="revision:r",
        revision_digest="b" * 64,
        supersedes=supersedes,
        event_type="action/unit",
        evidence_refs=("span:1",),
        ids=calls,
    )
    assert calls.log == [("ids", "snapshot")]
    assert snapshot.snapshot_id == "snapshot:1" and snapshot.content == content
    assert snapshot.content_digest == domain_digest(
        "ENTITY_SNAPSHOT", "1.0.0", canonical_payload(content)
    )
    assert (snapshot.entity_type, snapshot.entity_id) == (EntityType.ACTION, "entity:r")
    assert revision.actor is actor and revision.snapshot_id == snapshot.snapshot_id
    assert actor.actor_id == "agent:action-service" and actor.kind is ActorKind.AGENT
    assert actor.role == "action-planner"
    assert revision.parent_revision_digests == (() if supersedes is None else (supersedes,))
    assert revision.reason == "action/unit" and revision.evidence_refs == ("span:1",)
    assert revision.revision_id == "revision:r" and revision.created_at == BASE
    assert revision.affected_refs == () and revision.revision_digest == "b" * 64
