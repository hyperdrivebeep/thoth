"""Original-pinned Action draft behavior: port calls, ids, clock, errors and ledger data (90)."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast

import pytest

from thoth.application.services.action_effect_policy import classify_effect_vector, effect_impact
from thoth.application.services.action_service import ActionService
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.enums import ActorKind, CutoffState, EntityType
from thoth.domain.revision import ImpactPropagationPlan

BASE = datetime(2026, 9, 1, tzinfo=UTC)
PROJECT = "project:u"
OBJECT = "object:u"
Entry = tuple[Any, ...]


class World:
    """Records every port call in order and can fail a chosen call after it is recorded."""

    def __init__(self) -> None:
        self.log: list[Entry] = []
        self.fail: dict[Entry, Exception] = {}
        self.counter = 0
        self.ticks = 0
        self.actions: list[Any] = []
        self.portfolios: list[Any] = []
        self.plans: list[Any] = []
        self.audits: list[Any] = []
        self.changesets: list[Any] = []
        self.committed: tuple[str, ...] = ("revision",)
        self.missing_object = False
        self.evidence_state = CutoffState.ELIGIBLE
        self.hypothesis_object = OBJECT

    def note(self, *entry: Any) -> None:
        self.log.append(entry)
        error = self.fail.get(entry)
        if error is not None:
            raise error

    def shape(self) -> list[Entry]:
        """The call log without record identifiers; ids keep their prefix."""

        return [item if item[0] == "ids" else item[:1] for item in self.log]


class Ids:
    def __init__(self, world: World) -> None:
        self.world = world

    def new(self, prefix: str) -> str:
        self.world.note("ids", prefix)
        self.world.counter += 1
        return f"{prefix}:{self.world.counter}"


class Clock:
    def __init__(self, world: World) -> None:
        self.world = world

    def now(self) -> datetime:
        self.world.note("clock")
        self.world.ticks += 1
        return BASE + timedelta(seconds=self.world.ticks)


def latest(records: list[Any], key: str) -> dict[str, Any]:
    return {getattr(item, key): item for item in records}


class Store:
    def __init__(self, world: World) -> None:
        self.world = world

    def list_actions(self, project_id: str) -> tuple[Any, ...]:
        self.world.note("store.list_actions")
        return tuple(latest(self.world.actions, "action_id").values())

    def read_action(self, project_id: str, action_id: str, digest: str | None) -> Any:
        self.world.note("store.read_action", action_id)
        return latest(self.world.actions, "action_id").get(action_id)

    def add_action(self, record: Any) -> None:
        self.world.note("store.add_action", record.action_revision_id)
        self.world.actions.append(record)

    def read_portfolio(self, project_id: str, portfolio_id: str, digest: str | None) -> Any:
        self.world.note("store.read_portfolio", portfolio_id)
        return latest(self.world.portfolios, "portfolio_id").get(portfolio_id)

    def add_portfolio(self, record: Any) -> None:
        self.world.note("store.add_portfolio", record.portfolio_revision_id)
        self.world.portfolios.append(record)

    def read_plan(self, project_id: str, plan_id: str, digest: str | None) -> Any:
        self.world.note("store.read_plan", plan_id)
        return latest(self.world.plans, "plan_id").get(plan_id)

    def add_plan(self, record: Any) -> None:
        self.world.note("store.add_plan", record.plan_revision_id)
        self.world.plans.append(record)

    def list_authorizations(self, project_id: str, plan_id: str) -> tuple[Any, ...]:
        self.world.note("store.list_authorizations", plan_id)
        return ()

    def append_audit(self, record: Any) -> None:
        self.world.note("store.append_audit", record.event_type)
        self.world.audits.append(record)


class Objects:
    def __init__(self, world: World) -> None:
        self.world = world

    def read_object(self, project_id: str, object_id: str, digest: str | None) -> Any:
        self.world.note("objects.read_object", object_id)
        return None if self.world.missing_object else SimpleNamespace()


class Hypotheses:
    def __init__(self, world: World) -> None:
        self.world = world

    def read_hypothesis(self, project_id: str, reference: str, digest: str | None) -> Any:
        self.world.note("hypotheses.read_hypothesis", reference)
        return SimpleNamespace(object_id=self.world.hypothesis_object)


class Artifacts:
    def __init__(self, world: World) -> None:
        self.world = world

    def read_evidence(self, reference: str) -> Any:
        self.world.note("artifacts.read_evidence", reference)
        return SimpleNamespace(project_id=PROJECT, cutoff_state=self.world.evidence_state)


class Scope:
    def __init__(self, world: World, name: str) -> None:
        self.world = world
        self.name = name

    @contextmanager
    def transaction(self) -> Generator[None]:
        self.world.note(self.name + ".enter")
        try:
            yield
        finally:
            self.world.note(self.name + ".exit")


class Commits:
    def __init__(self, world: World) -> None:
        self.world = world

    @property
    def commit(self) -> Any:
        self.world.note("commits.lookup")
        return self._commit

    def _commit(self, changeset: Any) -> Any:
        self.world.note("commits.commit", changeset.changeset_id)
        self.world.changesets.append(changeset)
        return SimpleNamespace(committed_revision_ids=self.world.committed)


def build() -> tuple[ActionService, World]:
    world = World()
    service = ActionService(
        store=cast(Any, Store(world)),
        objects=cast(Any, Objects(world)),
        hypotheses=cast(Any, Hypotheses(world)),
        artifacts=cast(Any, Artifacts(world)),
        governance=cast(Any, object()),
        ledger=cast(Any, Scope(world, "ledger")),
        commits=cast(Any, Commits(world)),
        semantic_uow=cast(Any, Scope(world, "uow")),
        clock=cast(Any, Clock(world)),
        ids=cast(Any, Ids(world)),
    )
    return service, world


def spec(**changes: object) -> dict[str, object]:
    base: dict[str, object] = {
        "description": "unit action",
        "expected_observation_or_change": {"description": "observation"},
        "effect_completeness_confirmed": True,
        "stop_conditions": ("stop",),
        "observability": "receipt",
        "effect_vector": {"effect_completeness_confirmed": True},
    }
    base.update(changes)
    return base


def create(service: ActionService, **changes: Any) -> Any:
    arguments: dict[str, Any] = {
        "project_id": PROJECT,
        "object_id": OBJECT,
        "portfolio_id": "portfolio:u",
        "hypothesis_refs": (),
        "primary_purpose": "ANALYSIS_COMPUTATION",
        "secondary_purposes": (),
        "specification": spec(),
        "evidence_refs": ("span:1",),
    }
    arguments.update(changes)
    return service.create(**arguments)


CREATED = [
    ("objects.read_object",),
    ("artifacts.read_evidence",),
    ("store.list_actions",),
    ("ids", "action-revision"),
    ("ids", "action"),
    ("clock",),
    ("uow.enter",),
    ("ids", "snapshot"),
    ("commits.lookup",),
    ("ids", "changeset"),
    ("commits.commit",),
    ("store.add_action",),
    ("clock",),
    ("ids", "action-audit"),
    ("store.append_audit",),
    ("uow.exit",),
]
PERSIST = [
    ("uow.enter",),
    ("ids", "snapshot"),
    ("commits.lookup",),
    ("ids", "changeset"),
    ("commits.commit",),
]
AUDIT = [("clock",), ("ids", "action-audit"), ("store.append_audit",)]


def test_create_calls_ports_in_the_original_order_with_original_ids() -> None:
    service, world = build()
    record, duplicates, commit = create(service)
    assert world.shape() == CREATED
    assert world.log[3:5] == [("ids", "action-revision"), ("ids", "action")]
    assert (record.action_revision_id, record.action_id) == ("action-revision:1", "action:2")
    assert [item[1] for item in world.log if item[0] == "ids"] == [
        "action-revision",
        "action",
        "snapshot",
        "changeset",
        "action-audit",
    ]
    assert duplicates == () and commit.committed_revision_ids == ("revision",)
    assert world.actions == [record]
    assert record.created_at == BASE + timedelta(seconds=1)
    assert world.audits[0].created_at == BASE + timedelta(seconds=2)
    assert world.audits[0].payload == {"revision": record.revision_digest}


def test_create_builds_the_complete_candidate_record() -> None:
    service, _ = build()
    record, _, _ = create(service)
    effect: dict[str, object] = {"effect_completeness_confirmed": True}
    tier, policy, processes, roles = classify_effect_vector(effect)
    assert record.specification == {**spec(), "missing_fields": ()}
    assert record.expected_observation_or_change == {"description": "observation"}
    assert record.effect_vector == effect and record.impact_set == effect_impact(effect)
    assert (record.risk_tier, record.policy_state) == (tier, policy)
    assert (record.required_processes, record.required_roles) == (processes, roles)
    assert record.proposal_state == "CANDIDATE" and record.decision_state == "NOT_EVALUATED"
    assert record.freshness == "CURRENT" and record.supersedes_revision_digest is None
    assert (record.project_id, record.object_id, record.portfolio_id) == (
        PROJECT,
        OBJECT,
        "portfolio:u",
    )
    assert record.evidence_refs == ("span:1",) and record.hypothesis_refs == ()
    assert len(record.revision_digest) == 64


@pytest.mark.parametrize(
    ("changes", "missing"),
    [
        ({}, ()),
        ({"expected_observation_or_change": None}, ()),
        ({"expected_observation_or_change": {}}, ("expected_observation_or_change",)),
        ({"effect_completeness_confirmed": False}, ("effect_completeness_confirmed",)),
        ({"stop_conditions": ()}, ("stop_conditions",)),
        ({"observability": ""}, ("observability",)),
        (
            {
                "expected_observation_or_change": {},
                "effect_completeness_confirmed": False,
                "stop_conditions": (),
                "observability": "",
            },
            (
                "expected_observation_or_change",
                "effect_completeness_confirmed",
                "stop_conditions",
                "observability",
            ),
        ),
    ],
)
def test_create_reports_missing_fields_in_the_original_order(
    changes: dict[str, object], missing: tuple[str, ...]
) -> None:
    service, _ = build()
    specification = spec(**changes)
    if "effect_vector" not in changes and "effect_completeness_confirmed" in changes:
        specification["effect_vector"] = {}
    record, _, _ = create(service, specification=specification)
    assert record.specification["missing_fields"] == missing
    assert record.proposal_state == ("DRAFT" if missing else "CANDIDATE")


def test_create_maps_a_missing_expectation_exactly() -> None:
    service, _ = build()
    unresolved, _, _ = create(service, specification=spec(expected_observation_or_change=None))
    assert unresolved.expected_observation_or_change == {
        "description": "UNRESOLVED",
        "status": "MISSING",
    }
    unresolved_text, _, _ = create(
        service, specification=spec(expected_observation_or_change="a sentence")
    )
    assert unresolved_text.expected_observation_or_change == {
        "description": "UNRESOLVED",
        "status": "MISSING",
    }
    keyed, _, _ = create(
        service, specification=spec(expected_observation_or_change={"one": 1, "two": ("a", "b")})
    )
    assert keyed.expected_observation_or_change == {"one": 1, "two": ("a", "b")}


@pytest.mark.parametrize(
    "effect",
    [
        {"effect_completeness_confirmed": True},
        {"effect_completeness_confirmed": True, "external_write": True},
        {"effect_completeness_confirmed": True, "physical_action": True},
        {"effect_completeness_confirmed": False, "grants_waiver": True},
        {"external_write": True},
    ],
)
def test_create_follows_the_effect_classification_and_authorization_state(
    effect: dict[str, object],
) -> None:
    service, _ = build()
    record, _, _ = create(
        service,
        specification=spec(
            effect_vector=effect,
            effect_completeness_confirmed=effect.get("effect_completeness_confirmed", False),
        ),
    )
    tier, policy, processes, roles = classify_effect_vector(record.effect_vector)
    assert (record.risk_tier, record.policy_state) == (tier, policy)
    assert (record.required_processes, record.required_roles) == (processes, roles)
    assert record.impact_set == effect_impact(record.effect_vector)
    assert record.authorization_state == (
        "NOT_REQUIRED" if tier in {"R0", "R1", "R2", "R4"} else "NOT_PREPARED"
    )


def test_create_reports_duplicates_by_raw_specification_but_not_invalidated_ones() -> None:
    service, world = build()
    first, _, _ = create(service)
    _, none, _ = create(service)
    assert none == ()  # the stored specification carries missing_fields, so it is not equal
    world.actions[0] = first.model_copy(update={"specification": spec()})
    _, duplicates, _ = create(service)
    assert duplicates == (first.action_id,)
    world.actions[0] = world.actions[0].model_copy(update={"freshness": "INVALIDATED"})
    _, again, _ = create(service)
    assert again == ()


OBJ = ("objects.read_object",)
EVD = ("artifacts.read_evidence",)
HYP = ("hypotheses.read_hypothesis",)


@pytest.mark.parametrize(
    ("changes", "calls"),
    [
        ({"missing_object": True}, [OBJ]),
        ({"purpose": "NOT_A_PURPOSE"}, [OBJ]),
        ({"evidence_state": CutoffState.AFTER_CUTOFF}, [OBJ, EVD]),
        ({"hypothesis_object": "object:other", "refs": ("hypothesis:1",)}, [OBJ, EVD, HYP]),
        ({"effect": {"external_write": "yes"}}, [OBJ, EVD]),
    ],
)
def test_create_refuses_before_any_list_read_or_id(
    changes: dict[str, Any], calls: list[Entry]
) -> None:
    service, world = build()
    world.missing_object = changes.get("missing_object", False)
    world.evidence_state = changes.get("evidence_state", CutoffState.ELIGIBLE)
    world.hypothesis_object = changes.get("hypothesis_object", OBJECT)
    arguments: dict[str, Any] = {}
    if "purpose" in changes:
        arguments["primary_purpose"] = changes["purpose"]
    if "refs" in changes:
        arguments["hypothesis_refs"] = changes["refs"]
    if "effect" in changes:
        arguments["specification"] = spec(effect_vector=changes["effect"])
    with pytest.raises(ValueError):
        create(service, **arguments)
    assert world.shape() == calls
    assert world.actions == [] and world.audits == []


@pytest.mark.parametrize(
    ("failing", "expected_tail"),
    [
        (("store.list_actions",), [("store.list_actions",)]),
        (
            ("ids", "action"),
            [("store.list_actions",), ("ids", "action-revision"), ("ids", "action")],
        ),
        (
            ("clock",),
            [
                ("store.list_actions",),
                ("ids", "action-revision"),
                ("ids", "action"),
                ("clock",),
            ],
        ),
    ],
)
def test_create_stops_at_the_failing_port_with_the_same_exception(
    failing: Entry, expected_tail: list[Entry]
) -> None:
    service, world = build()
    error = RuntimeError("boom")
    world.fail[failing] = error
    with pytest.raises(RuntimeError) as raised:
        create(service)
    assert raised.value is error
    assert world.shape()[2:] == expected_tail
    assert world.actions == [] and world.audits == []


def test_create_failure_inside_the_transaction_leaves_no_later_call() -> None:
    service, world = build()
    error = RuntimeError("commit failed")
    world.fail[("commits.commit", "changeset:4")] = error
    with pytest.raises(RuntimeError) as raised:
        create(service)
    assert raised.value is error
    assert world.shape()[-2:] == [("commits.commit",), ("uow.exit",)]
    assert world.actions == [] and world.audits == []


def test_a_branched_commit_is_refused_after_the_commit_call() -> None:
    service, world = build()
    world.committed = ()
    with pytest.raises(
        ValueError, match="Action commit branched because expected revision changed"
    ):
        create(service)
    assert world.shape()[-3:] == [("ids", "changeset"), ("commits.commit",), ("uow.exit",)]
    assert world.actions == []


def test_commit_data_for_a_new_action_matches_the_record() -> None:
    service, world = build()
    record, _, _ = create(service)
    (changeset,) = world.changesets
    (staged,) = changeset.staged_revisions
    snapshot, revision = staged.snapshot, staged.revision
    content = record.model_dump(mode="python")
    assert changeset.changeset_id == "changeset:4" and snapshot.snapshot_id == "snapshot:3"
    assert changeset.project_id == PROJECT and changeset.expected_heads == {}
    assert changeset.impact_plan == ImpactPropagationPlan()
    assert changeset.reason == revision.reason == "action/created"
    assert (snapshot.entity_type, snapshot.entity_id) == (EntityType.ACTION, record.action_id)
    assert snapshot.schema_version == "1.0.0" and snapshot.project_id == PROJECT
    assert snapshot.content == content
    assert snapshot.content_digest == domain_digest(
        "ENTITY_SNAPSHOT", "1.0.0", canonical_payload(content)
    )
    assert revision.revision_id == record.action_revision_id
    assert (revision.entity_type, revision.entity_id) == (EntityType.ACTION, record.action_id)
    assert revision.snapshot_id == snapshot.snapshot_id and revision.project_id == PROJECT
    assert revision.parent_revision_digests == () and revision.affected_refs == ()
    assert revision.evidence_refs == record.evidence_refs == ("span:1",)
    assert revision.revision_digest == record.revision_digest
    assert revision.created_at == record.created_at
    assert revision.actor is changeset.actor
    assert revision.actor.actor_id == "agent:action-service"
    assert revision.actor.kind is ActorKind.AGENT and revision.actor.role == "action-planner"


def test_revise_keeps_the_commit_evidence_and_parent_data() -> None:
    service, world = build()
    first, _, _ = create(service)
    world.log.clear()
    revised, _ = service.revise(
        first,
        updates={"primary_purpose": "SIMULATION"},
        event_type="action/updated",
        evidence_refs=("span:2",),
    )
    changeset = world.changesets[-1]
    (staged,) = changeset.staged_revisions
    assert changeset.expected_heads == {"ACTION:" + first.action_id: first.revision_digest}
    assert staged.revision.parent_revision_digests == (first.revision_digest,)
    assert staged.revision.evidence_refs == ("span:2",)
    assert revised.supersedes_revision_digest == first.revision_digest
    assert staged.revision.actor is changeset.actor


@pytest.mark.parametrize(
    ("failing", "suffix"),
    [
        (("ids", "snapshot"), [("uow.enter",), ("ids", "snapshot"), ("uow.exit",)]),
        (
            ("ids", "changeset"),
            [
                ("uow.enter",),
                ("ids", "snapshot"),
                ("commits.lookup",),
                ("ids", "changeset"),
                ("uow.exit",),
            ],
        ),
    ],
)
def test_commit_data_failures_stop_in_the_original_order(
    failing: Entry, suffix: list[Entry]
) -> None:
    service, world = build()
    error = RuntimeError("boom")
    world.fail[failing] = error
    with pytest.raises(RuntimeError) as raised:
        create(service)
    assert raised.value is error
    assert world.shape()[6:] == suffix
    assert world.changesets == [] and world.actions == []


def two_actions(service: ActionService, **second: Any) -> tuple[Any, Any]:
    first, _, _ = create(service)
    other: dict[str, Any] = {
        "primary_purpose": "SIMULATION",
        "specification": spec(description="two"),
    }
    other.update(second)
    right, _, _ = create(service, **other)
    return first, right


def compose(service: ActionService, actions: tuple[Any, ...], **changes: Any) -> Any:
    arguments: dict[str, Any] = {
        "project_id": PROJECT,
        "object_id": OBJECT,
        "action_ids": tuple(item.action_id for item in actions),
        "decision_need": "choose",
        "criteria_proposal": (),
    }
    arguments.update(changes)
    return service.compose_portfolio(**arguments)


COMPOSED = [
    ("objects.read_object",),
    ("store.read_action",),
    ("store.read_action",),
    ("ids", "action-portfolio"),
    ("store.read_portfolio",),
    ("ids", "action-portfolio-revision"),
    ("clock",),
    *PERSIST,
    ("store.add_portfolio",),
    *AUDIT,
    ("uow.exit",),
]


def test_compose_portfolio_orders_ids_reads_and_clock_and_sets_defaults() -> None:
    service, world = build()
    actions = two_actions(service)
    world.log.clear()
    portfolio, _ = compose(service, actions)
    assert world.shape() == COMPOSED
    assert portfolio.portfolio_id.startswith("action-portfolio:")
    assert portfolio.portfolio_revision_id.startswith("action-portfolio-revision:")
    assert portfolio.mandatory_criteria == (
        {
            "criterion_id": "mandatory:policy",
            "name": "policy admissibility",
            "mandatory": True,
            "rationale": "prohibited actions cannot enter the feasible set",
        },
    )
    assert portfolio.enhancing_criteria == ()
    assert portfolio.action_refs == tuple(item.action_id for item in actions)
    assert portfolio.decision_need == "choose" and portfolio.evaluation_method == "NOT_EVALUATED"
    assert (portfolio.uncertainty, portfolio.sensitivity) == ("UNASSESSED", "UNASSESSED")
    assert portfolio.value_of_information == "UNASSESSED"
    assert portfolio.scenario_results == () and portfolio.decision_state == "NOT_EVALUATED"
    assert portfolio.supersedes_revision_digest is None
    assert portfolio.created_at == BASE + timedelta(seconds=world.ticks - 1)
    (changeset,) = world.changesets[-1:]
    assert changeset.expected_heads == {}
    assert changeset.staged_revisions[0].revision.evidence_refs == ()


def test_compose_portfolio_splits_criteria_and_keeps_the_supplied_id() -> None:
    service, world = build()
    actions = two_actions(service)
    criteria = (
        {"criterion_id": "m1", "mandatory": True},
        {"criterion_id": "e1"},
        {"criterion_id": "e2", "mandatory": False},
    )
    world.log.clear()
    portfolio, _ = compose(
        service, actions, criteria_proposal=criteria, portfolio_id="portfolio:given"
    )
    assert ("ids", "action-portfolio") not in world.shape()
    assert portfolio.portfolio_id == "portfolio:given"
    assert portfolio.mandatory_criteria == (criteria[0],)
    assert portfolio.enhancing_criteria == (criteria[1], criteria[2])


def test_compose_portfolio_supersedes_an_existing_head() -> None:
    service, world = build()
    actions = two_actions(service)
    first, _ = compose(service, actions, portfolio_id="portfolio:given")
    world.log.clear()
    second, _ = compose(service, actions, portfolio_id="portfolio:given")
    assert second.supersedes_revision_digest == first.revision_digest
    assert world.changesets[-1].expected_heads == {"ACTION:portfolio:given": first.revision_digest}
    assert world.changesets[-1].staged_revisions[0].revision.parent_revision_digests == (
        first.revision_digest,
    )


@pytest.mark.parametrize("case", ["cross_object", "single", "same_purpose"])
def test_compose_portfolio_refuses_before_ids_and_clock(case: str) -> None:
    service, world = build()
    actions = two_actions(service)
    chosen: tuple[Any, ...] = actions
    if case == "cross_object":
        world.actions[0] = world.actions[0].model_copy(update={"object_id": "object:other"})
    elif case == "single":
        chosen = actions[:1]
    else:
        world.actions[1] = world.actions[1].model_copy(
            update={"primary_purpose": world.actions[0].primary_purpose}
        )
    world.log.clear()
    with pytest.raises(ValueError):
        compose(service, chosen)
    shape = world.shape()
    assert not any(item[0] == "ids" for item in shape) and ("clock",) not in shape
    assert ("store.read_portfolio",) not in shape and ("uow.enter",) not in shape


def make_portfolio(
    service: ActionService, world: World, *effects: dict[str, object]
) -> tuple[Any, tuple[Any, ...]]:
    purposes = ("ANALYSIS_COMPUTATION", "SIMULATION", "INFORMATION_ACQUISITION")
    actions = tuple(
        create(
            service,
            primary_purpose=purposes[index],
            specification=spec(
                description=str(index),
                effect_vector=effect,
                effect_completeness_confirmed=effect.get("effect_completeness_confirmed", False),
            ),
        )[0]
        for index, effect in enumerate(effects)
    )
    portfolio, _ = compose(service, actions, portfolio_id="portfolio:eval")
    world.log.clear()
    return portfolio, actions


OK: dict[str, object] = {"effect_completeness_confirmed": True}
BLOCKED: dict[str, object] = {"effect_completeness_confirmed": False, "grants_waiver": True}


@pytest.mark.parametrize(
    ("effects", "preferences", "state", "sensitivity", "information"),
    [
        (
            (OK, OK),
            (),
            "PREFERENCE_REQUIRED",
            "RANK_UNSTABLE_WITHOUT_PREFERENCES",
            "PREFERENCE_INPUT_HAS_DECISION_VALUE",
        ),
        (
            (OK, OK),
            ({"weight": 1},),
            "RECOMMENDATION_READY",
            "STABLE_WITH_SUPPLIED_PREFERENCES",
            "NO_ADDITIONAL_PREFERENCE_REQUIRED",
        ),
        (
            (OK, BLOCKED),
            (),
            "RECOMMENDATION_READY",
            "STABLE_WITH_SUPPLIED_PREFERENCES",
            "NO_ADDITIONAL_PREFERENCE_REQUIRED",
        ),
        (
            (BLOCKED, BLOCKED),
            (),
            "EVALUATED",
            "STABLE_WITH_SUPPLIED_PREFERENCES",
            "NO_ADDITIONAL_PREFERENCE_REQUIRED",
        ),
    ],
)
def test_evaluate_portfolio_states_gates_and_extra_audit(
    effects: tuple[dict[str, object], ...],
    preferences: tuple[dict[str, object], ...],
    state: str,
    sensitivity: str,
    information: str,
) -> None:
    service, world = build()
    portfolio, actions = make_portfolio(service, world, *effects)
    revised, _ = service.evaluate_portfolio(
        portfolio,
        evaluation_method="method:u",
        policy_criteria_ref="policy:u",
        preference_inputs=preferences,
    )
    assert world.shape() == [
        ("store.read_action",),
        ("store.read_action",),
        ("ids", "action-portfolio-revision"),
        ("clock",),
        *PERSIST,
        ("store.add_portfolio",),
        *AUDIT,
        *AUDIT,
        ("uow.exit",),
    ]
    assert revised.decision_state == state and revised.sensitivity == sensitivity
    assert revised.value_of_information == information
    assert revised.evaluation_method == "method:u"
    assert revised.uncertainty == "EXPLICIT_SCENARIO_COMPARISON"
    assert revised.recommendation_ref is None
    assert revised.supersedes_revision_digest == portfolio.revision_digest
    assert [item["action_id"] for item in revised.scenario_results] == [
        item.action_id for item in actions
    ]
    for result, action in zip(revised.scenario_results, actions, strict=True):
        assert result == {
            "action_id": action.action_id,
            "mandatory_gate": "FAIL" if action.policy_state == "PROHIBITED" else "PASS",
            "risk_tier": action.risk_tier,
            "impact": action.impact_set,
            "expected": action.expected_observation_or_change,
        }
    assert world.audits[-2].payload == {"revision": revised.revision_digest}
    assert world.audits[-1].payload == {"policy_criteria_ref": "policy:u"}


def test_recommend_picks_the_lowest_tier_and_keeps_the_sensitivity() -> None:
    service, world = build()
    portfolio, actions = make_portfolio(
        service, world, {"effect_completeness_confirmed": True, "external_write": True}, OK
    )
    revised, _ = service.recommend(portfolio, "because")
    assert revised.recommendation_ref == actions[1].action_id
    assert revised.decision_state == "RECOMMENDATION_READY"
    assert revised.sensitivity == portfolio.sensitivity
    assert world.audits[-1].payload == {"rationale": "because", "selection_implied": False}
    assert world.shape()[:2] == [("store.read_action",), ("store.read_action",)]


def test_recommend_refuses_when_nothing_is_admissible_before_any_id() -> None:
    service, world = build()
    portfolio, _ = make_portfolio(service, world, BLOCKED, BLOCKED)
    with pytest.raises(ValueError, match="no policy-admissible Action can be recommended"):
        service.recommend(portfolio, "because")
    assert world.shape() == [("store.read_action",), ("store.read_action",)]


def test_revise_portfolio_never_changes_the_supplied_updates() -> None:
    service, world = build()
    portfolio, _ = make_portfolio(service, world, OK, OK)
    updates: dict[str, object] = {"decision_state": "UNIT", "recommendation_ref": "action:x"}
    snapshot_of_updates = dict(updates)
    revised, _ = service._revise_portfolio(  # pyright: ignore[reportPrivateUsage]
        portfolio, updates=updates, event_type="action/unit", audit_payload=None
    )
    assert updates == snapshot_of_updates
    assert (revised.decision_state, revised.recommendation_ref) == ("UNIT", "action:x")
    assert revised.supersedes_revision_digest == portfolio.revision_digest
    assert revised.revision_digest != portfolio.revision_digest
    assert revised.created_at == BASE + timedelta(seconds=world.ticks - 1)
    assert world.shape()[:2] == [("ids", "action-portfolio-revision"), ("clock",)]
    assert world.audits[-1].event_type == "action/unit" and len(world.audits) == 4


def step(**changes: object) -> dict[str, object]:
    base: dict[str, object] = {
        "effect_vector": {"effect_completeness_confirmed": True, "external_write": False},
        "state": "READY",
        "inputs": [],
    }
    base.update(changes)
    return base


def compose_plan(service: ActionService, action: Any, **changes: Any) -> Any:
    arguments: dict[str, Any] = {
        "project_id": PROJECT,
        "object_id": OBJECT,
        "selected_action_refs": (action.action_id,),
        "step_candidates": (step(),),
        "dependency_edges": (),
    }
    arguments.update(changes)
    return service.compose_plan(**arguments)


PLAN_COMPOSED = [
    ("objects.read_object",),
    ("store.read_action",),
    ("ids", "action-step"),
    ("ids", "action-plan"),
    ("store.read_plan",),
    ("ids", "action-plan-revision"),
    ("clock",),
    *PERSIST,
    ("store.add_plan",),
    *AUDIT,
    ("store.list_authorizations",),
    ("uow.exit",),
]


def test_compose_plan_orders_step_ids_plan_id_read_and_revision_id() -> None:
    service, world = build()
    action, _, _ = create(service)
    world.log.clear()
    plan, _ = compose_plan(service, action)
    assert world.shape() == PLAN_COMPOSED
    assert plan.plan_id.startswith("action-plan:") and plan.plan_revision_id.startswith(
        "action-plan-revision:"
    )
    (normalized,) = plan.steps
    assert str(normalized["step_id"]).startswith("action-step:")
    derived = service._derive_plan(plan.steps, ())  # pyright: ignore[reportPrivateUsage]
    for key, expected in derived.items():
        assert getattr(plan, key) == expected
    assert plan.validation_state == "VALIDATED" and plan.freshness == "CURRENT"
    assert plan.authorization_refs == () and plan.supersedes_revision_digest is None
    assert plan.selected_action_refs == (action.action_id,) and plan.dependency_edges == ()
    assert plan.created_at == BASE + timedelta(seconds=world.ticks - 1)
    changeset = world.changesets[-1]
    assert changeset.expected_heads == {}
    assert changeset.staged_revisions[0].revision.evidence_refs == ()


def test_compose_plan_with_a_given_id_and_step_id_skips_those_ids() -> None:
    service, world = build()
    action, _, _ = create(service)
    world.log.clear()
    compose_plan(service, action, plan_id="plan:given", step_candidates=(step(step_id="step:a"),))
    shape = world.shape()
    assert ("ids", "action-step") not in shape and ("ids", "action-plan") not in shape
    assert shape.count(("ids", "action-plan-revision")) == 1


def test_compose_plan_supersedes_and_keeps_authorization_refs() -> None:
    service, world = build()
    action, _, _ = create(service)
    first, _ = compose_plan(service, action, plan_id="plan:given")
    world.plans[-1] = first.model_copy(update={"authorization_refs": ("authorization:1",)})
    world.log.clear()
    second, _ = compose_plan(service, action, plan_id="plan:given")
    assert second.supersedes_revision_digest == first.revision_digest
    assert second.authorization_refs == ("authorization:1",)
    assert world.changesets[-1].expected_heads == {"ACTION:plan:given": first.revision_digest}
    assert world.changesets[-1].staged_revisions[0].revision.parent_revision_digests == (
        first.revision_digest,
    )


@pytest.mark.parametrize("case", ["cross_object", "empty", "duplicate_step"])
def test_compose_plan_refuses_before_plan_ids_and_clock(case: str) -> None:
    service, world = build()
    action, _, _ = create(service)
    arguments: dict[str, Any] = {}
    if case == "cross_object":
        world.actions[0] = action.model_copy(update={"object_id": "object:other"})
    elif case == "empty":
        arguments["step_candidates"] = ()
    else:
        arguments["step_candidates"] = (step(step_id="s"), step(step_id="s"))
    world.log.clear()
    with pytest.raises(ValueError):
        compose_plan(service, action, **arguments)
    shape = world.shape()
    assert ("ids", "action-plan") not in shape and ("ids", "action-plan-revision") not in shape
    assert ("clock",) not in shape and ("store.read_plan",) not in shape
