"""Registration retains actual handler owners and connects the returned judgment wiring."""

from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest
from tests.atomicity.test_store_lifecycle import RecordingFactory

from thoth.adapters.storage.sqlite import SqliteLedger
from thoth.application.commands import (
    FieldMeasurementHandlers,
    InvestigationQueryHandlers,
    ProjectCommandHandlers,
    ProjectionQueryHandlers,
    ProjectPackCommandHandlers,
)
from thoth.application.commands.research_threads import ResearchThreadHandlers
from thoth.application.commands.revisions_full import RevisionFullHandlers
from thoth.application.commands.sources import SourceCommandHandlers
from thoth.apps.judgment_review_composition import JudgmentReviewWiring
from thoth.apps.runtime import create_runtime
from thoth.domain.deployment_mode import DeploymentMode
from thoth.protocol.bus import CommandBus
from thoth.protocol.registry import CommandHandler, MethodRegistry


@pytest.mark.parametrize("mode", (DeploymentMode.LOCAL, DeploymentMode.HOSTED_REVIEW))
def test_registration_reuses_created_handlers_and_judgment_wiring(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: DeploymentMode
) -> None:
    created: dict[type[Any], object] = {}
    methods: dict[str, tuple[MethodRegistry, CommandHandler]] = {}
    connected: list[tuple[JudgmentReviewWiring, CommandBus, ResearchThreadHandlers]] = []
    owners: dict[str, type[Any]] = {
        "project/read": ProjectCommandHandlers,
        "projectpack/list": ProjectPackCommandHandlers,
        "project/source/connect": SourceCommandHandlers,
        "revision/graph/read": RevisionFullHandlers,
        "action/preflight/read": ProjectionQueryHandlers,
        "investigation/read": InvestigationQueryHandlers,
        "field/protocol/seal": FieldMeasurementHandlers,
    }

    def capture(cls: type[Any]) -> None:
        original = cast(Callable[..., None], cls.__init__)

        def initialize(instance: object, *args: object, **kwargs: Any) -> None:
            original(instance, *args, **kwargs)
            created[cls] = instance

        monkeypatch.setattr(cls, "__init__", initialize)

    for cls in {*owners.values(), JudgmentReviewWiring, ResearchThreadHandlers}:
        capture(cls)
    original_register = MethodRegistry.register

    def register(registry: MethodRegistry, method: str, handler: CommandHandler) -> None:
        original_register(registry, method, handler)
        if method in owners:
            methods[method] = (registry, handler)

    monkeypatch.setattr(MethodRegistry, "register", register)
    original_connect = JudgmentReviewWiring.connect

    def connect(
        wiring: JudgmentReviewWiring, bus: CommandBus, entry: ResearchThreadHandlers
    ) -> None:
        connected.append((wiring, bus, entry))
        original_connect(wiring, bus, entry)

    monkeypatch.setattr(JudgmentReviewWiring, "connect", connect)
    runtime = create_runtime(tmp_path, deployment_mode=mode)
    try:
        assert set(methods) == set(owners)
        for method, cls in owners.items():
            registry, handler = methods[method]
            assert getattr(handler, "__self__", None) is created[cls]
            assert getattr(registry.resolve(method), "__self__", None) is created[cls]
            assert registry.registered_methods() == runtime.bus.registered_methods()
        assert connected == [
            (created[JudgmentReviewWiring], runtime.bus, created[ResearchThreadHandlers])
        ]
    finally:
        runtime.close()


@pytest.mark.parametrize("failed_method", ("revision/list", "hypothesis/review/request"))
def test_registration_table_failure_propagates_without_later_registration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed_method: str
) -> None:
    factory = RecordingFactory()
    calls: list[str] = []
    closed: list[SqliteLedger] = []
    original_register = MethodRegistry.register
    original_close = SqliteLedger.close

    def register(registry: MethodRegistry, method: str, handler: CommandHandler) -> None:
        calls.append(method)
        if method == failed_method:
            raise RuntimeError("registration table failed")
        original_register(registry, method, handler)

    def close(ledger: SqliteLedger) -> None:
        closed.append(ledger)
        original_close(ledger)

    monkeypatch.setattr(MethodRegistry, "register", register)
    monkeypatch.setattr(SqliteLedger, "close", close)
    with pytest.raises(RuntimeError, match="registration table failed"):
        create_runtime(tmp_path, storage_factory=factory, deployment_mode=DeploymentMode.LOCAL)
    assert calls[-1] == failed_method and calls.count(failed_method) == 1
    assert closed == [factory.bundles[0].ledger]
