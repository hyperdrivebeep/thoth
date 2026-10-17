"""Freeze startup profile transactions and the core command construction contract."""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import pytest
from tests.atomicity.test_store_lifecycle import RecordingFactory
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_runtime_acquisition_composition import Construction

from thoth.adapters.connectors import ConnectorRegistry
from thoth.adapters.connectors.project_public_web import ProjectPublicWebConnector
from thoth.adapters.storage.bundle import SqliteStoreBundle, SqliteStoreFactory
from thoth.adapters.storage.decision_object import SqliteDecisionObjectStore
from thoth.adapters.storage.outcome import SqliteOutcomeStore
from thoth.adapters.storage.sqlite import SqliteLedger
from thoth.application.commands import (
    ActionHandlers,
    CriterionFullHandlers,
    DecisionObjectHandlers,
    EvidenceCommandHandlers,
    ExecutionHandlers,
    HypothesisHandlers,
    OutcomeHandlers,
    ProjectCommandHandlers,
)
from thoth.application.services.decision_object_service import DecisionObjectService
from thoth.application.services.outcome_service import OutcomeService
from thoth.application.services.revision_service import RevisionCommitService
from thoth.apps.runtime import create_runtime
from thoth.domain.connectors import ConnectorCapability
from thoth.domain.decision_object_full import ObjectProfileRecord
from thoth.domain.deployment_mode import DeploymentMode
from thoth.domain.outcome_full import OutcomeProfileRecord

FIXTURE = Path(__file__).parents[1] / "fixtures/runtime_decision_profile_seeds.json"
ORDER = (
    "object_commit",
    "object",
    "object_seed",
    "outcome_commit",
    "outcome",
    "outcome_seed",
    "project",
    "evidence",
    "criterion",
    "object_handler",
    "hypothesis_handler",
    "action_handler",
    "execution_handler",
    "outcome_handler",
)


def profile_records(bundle: SqliteStoreBundle) -> dict[str, list[dict[str, Any]]]:
    return {
        "object_profiles": [
            p.model_dump(mode="json") for p in bundle.decision_objects().list_profiles(False)
        ],
        "outcome_profiles": [
            p.model_dump(mode="json") for p in bundle.outcomes().list_profiles(False)
        ],
    }


@dataclass
class DecisionCapture:
    fail_at: str | None = None
    events: list[str] = field(default_factory=list)
    calls: dict[str, Construction] = field(default_factory=dict)
    active: bool = False
    finished: bool = False

    def note(
        self, label: str, instance: object, args: tuple[object, ...], kwargs: dict[str, Any]
    ) -> None:
        if label == "object_commit":
            self.active = True
        if self.active and not self.finished:
            self.events.append(label)
            self.calls[label] = Construction(instance, args, dict(kwargs))
            if label == self.fail_at:
                raise RuntimeError(f"decision construction failed: {label}")


def capture_decisions(
    monkeypatch: pytest.MonkeyPatch, fail_at: str | None = None
) -> DecisionCapture:
    captured = DecisionCapture(fail_at=fail_at)
    original_commit = RevisionCommitService.__init__

    def commit(instance: RevisionCommitService, *args: Any, **kwargs: Any) -> None:
        version = kwargs.get("policy_version")
        labels = {"decision-object:2.0.0": "object_commit", "outcome:2.0.0": "outcome_commit"}
        label = labels.get(version) if isinstance(version, str) else None
        if label:
            captured.note(label, instance, args, kwargs)
        original_commit(instance, *args, **kwargs)

    monkeypatch.setattr(RevisionCommitService, "__init__", commit)

    def install(label: str, cls: type[Any]) -> None:
        original = cast(Callable[..., None], cls.__init__)

        def construct(instance: object, *args: object, **kwargs: Any) -> None:
            captured.note(label, instance, args, kwargs)
            original(instance, *args, **kwargs)
            if label == "outcome_handler":
                captured.finished = True

        monkeypatch.setattr(cls, "__init__", construct)

    constructors: dict[str, type[Any]] = {
        "object": DecisionObjectService,
        "outcome": OutcomeService,
        "project": ProjectCommandHandlers,
        "evidence": EvidenceCommandHandlers,
        "criterion": CriterionFullHandlers,
        "object_handler": DecisionObjectHandlers,
        "hypothesis_handler": HypothesisHandlers,
        "action_handler": ActionHandlers,
        "execution_handler": ExecutionHandlers,
        "outcome_handler": OutcomeHandlers,
    }
    for label, cls in constructors.items():
        install(label, cls)

    def seed(label: str, cls: type[Any]) -> None:
        original = cast(Callable[[object], None], cls.seed_profiles)

        def run(instance: object) -> None:
            captured.note(label, instance, (), {})
            original(instance)

        monkeypatch.setattr(cls, "seed_profiles", run)

    seed("object_seed", DecisionObjectService)
    seed("outcome_seed", OutcomeService)
    return captured


def test_core_commands_preserve_order_sharing_and_seed_contents_on_reopen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = capture_decisions(monkeypatch)
    factory = RecordingFactory()
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
    runtime = create_runtime(
        tmp_path, storage_factory=factory, deployment_mode=DeploymentMode.LOCAL
    )
    try:
        assert captured.events == list(ORDER)
        c = captured.calls
        assert c["object_seed"].instance is c["object"].instance
        assert c["outcome_seed"].instance is c["outcome"].instance
        assert c["object"].kwargs["commits"] is c["object_commit"].instance
        assert c["outcome"].kwargs["commits"] is c["outcome_commit"].instance
        for owner in ("object", "outcome"):
            assert c[owner + "_commit"].args == (
                runtime.ledger,
                c[owner].kwargs["clock"],
                c[owner].kwargs["ids"],
            )
        assert c["object_handler"].kwargs["service"] is c["object"].instance
        assert c["outcome_handler"].kwargs["service"] is c["outcome"].instance
        assert c["object_handler"].kwargs["store"] is c["object"].kwargs["store"]
        assert c["outcome_handler"].kwargs["store"] is c["outcome"].kwargs["store"]
        for handler in ("hypothesis_handler", "action_handler"):
            assert c[handler].kwargs["objects"] is c["object"].kwargs["store"]
        assert c["outcome"].kwargs["objects"] is c["object"].kwargs["store"]
        assert c["execution_handler"].kwargs["store"] is c["outcome"].kwargs["executions"]
        assert c["execution_handler"].kwargs["actions"] is c["action_handler"].kwargs["store"]
        for label in ("object", "outcome", "project", "execution_handler"):
            assert c[label].kwargs["ledger"] is runtime.ledger
        for label in ("object", "outcome", "project"):
            assert c[label].kwargs["clock"] is c["object"].kwargs["clock"]
            assert c[label].kwargs["ids"] is c["object"].kwargs["ids"]
        for label in ("outcome", "project", "evidence", "hypothesis_handler"):
            assert c[label].kwargs["artifacts"] is c["object"].kwargs["artifacts"]
        assert c["project"].kwargs["store"] is factory.bundles[0].projects
        assert profile_records(factory.bundles[0]) == expected
    finally:
        runtime.close()
    reopened = create_runtime(
        tmp_path, storage_factory=factory, deployment_mode=DeploymentMode.LOCAL
    )
    try:
        assert profile_records(factory.bundles[1]) == expected
    finally:
        reopened.close()


@pytest.mark.parametrize("fail_at", ORDER)
def test_core_construction_failure_keeps_sequence_and_closes_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail_at: str
) -> None:
    captured = capture_decisions(monkeypatch, fail_at)
    factory = RecordingFactory()
    closed: list[SqliteLedger] = []
    original = SqliteLedger.close

    def close(ledger: SqliteLedger) -> None:
        closed.append(ledger)
        original(ledger)

    monkeypatch.setattr(SqliteLedger, "close", close)
    with pytest.raises(RuntimeError, match=f"decision construction failed: {fail_at}"):
        create_runtime(tmp_path, storage_factory=factory, deployment_mode=DeploymentMode.LOCAL)
    assert captured.events == list(ORDER[: ORDER.index(fail_at) + 1])
    assert closed == [factory.bundles[0].ledger]


@pytest.mark.parametrize("owner", ("object", "outcome"))
def test_each_seed_rolls_back_itself_without_erasing_the_previous_seed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, owner: str
) -> None:
    calls = 0
    before: list[dict[str, Any]] = []
    factory = RecordingFactory()

    def observe(store: SqliteDecisionObjectStore | SqliteOutcomeStore) -> None:
        nonlocal calls
        if calls == 0:
            before.extend(p.model_dump(mode="json") for p in store.list_profiles(False))
        calls += 1
        if calls == 2:
            raise RuntimeError("profile publication failed")

    if owner == "object":
        original_object = SqliteDecisionObjectStore.put_profile

        def put_object(store: SqliteDecisionObjectStore, value: ObjectProfileRecord) -> None:
            observe(store)
            original_object(store, value)

        monkeypatch.setattr(SqliteDecisionObjectStore, "put_profile", put_object)
    else:
        original_outcome = SqliteOutcomeStore.put_profile

        def put_outcome(store: SqliteOutcomeStore, value: OutcomeProfileRecord) -> None:
            observe(store)
            original_outcome(store, value)

        monkeypatch.setattr(SqliteOutcomeStore, "put_profile", put_outcome)
    with pytest.raises(RuntimeError, match="profile publication failed"):
        create_runtime(tmp_path, storage_factory=factory, deployment_mode=DeploymentMode.LOCAL)
    assert calls == 2
    reopened = SqliteStoreFactory().open(tmp_path)
    try:
        actual = profile_records(reopened)
        assert actual[owner + "_profiles"] == before
        if owner == "outcome":
            assert (
                actual["object_profiles"]
                == json.loads(FIXTURE.read_text(encoding="utf-8"))["object_profiles"]
            )
        else:
            assert actual["outcome_profiles"] == []
    finally:
        reopened.close()


class ObservedRegistry(ConnectorRegistry):
    def __init__(self) -> None:
        super().__init__()
        self.reads: list[tuple[str, ...]] = []

    def capabilities(self) -> tuple[ConnectorCapability, ...]:
        values = super().capabilities()
        self.reads.append(tuple(item.connector_id for item in values))
        return values


async def test_public_project_reads_consume_the_current_registry(tmp_path: Path) -> None:
    registry = ObservedRegistry()
    runtime = create_runtime(
        tmp_path, connector_registry=registry, deployment_mode=DeploymentMode.LOCAL
    )
    try:
        before = len(registry.reads)
        value(
            await runtime.bus.dispatch(
                request(
                    "project/create",
                    "create",
                    {
                        "project_id": "project:registry",
                        "name": "Registry",
                        "cutoff_at": "2026-09-19T00:00:00Z",
                    },
                )
            )
        )
        assert len(registry.reads) > before and registry.reads[-1] == ()
        registry.register(ProjectPublicWebConnector(lambda _project: ()))
        before = len(registry.reads)
        value(
            await runtime.bus.query(
                request("project/read", "read", {"project_id": "project:registry"})
            )
        )
        assert len(registry.reads) > before and registry.reads[-1] == ("project-public-web",)
    finally:
        runtime.close()
