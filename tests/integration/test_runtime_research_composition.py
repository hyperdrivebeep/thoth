"""Keep source, history and query assembly interleaved with their original neighbors."""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import pytest
from tests.atomicity.test_store_lifecycle import RecordingFactory
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_runtime_acquisition_composition import Construction

from thoth.adapters.projectpacks import FilesystemProjectPackLoader
from thoth.adapters.storage.scoped_ledger import ScopedLedger
from thoth.adapters.storage.sqlite import SqliteLedger
from thoth.application.commands import (
    FieldMeasurementHandlers,
    InvestigationQueryHandlers,
    ProjectionQueryHandlers,
    ProjectPackCommandHandlers,
    ThreadCommandHandlers,
)
from thoth.application.commands.closure_full import ClosureHandlers
from thoth.application.commands.lifecycle import LifecycleCommandHandlers
from thoth.application.commands.memory_full import MemoryFullHandlers
from thoth.application.commands.receipts_full import ReceiptHandlers
from thoth.application.commands.revisions import RevisionCommandHandlers
from thoth.application.commands.revisions_full import RevisionFullHandlers
from thoth.application.commands.sources import SourceCommandHandlers
from thoth.application.commands.thread_analysis import ThreadAnalysisCommandHandlers
from thoth.application.services.scoped_receipt_dag import ScopedReceiptControls
from thoth.apps.projectpack_execution import ProjectPackExecutionFactory
from thoth.apps.runtime import create_runtime
from thoth.domain.deployment_mode import DeploymentMode

ORDER = (
    "thread",
    "loader",
    "pack_factory",
    "pack",
    "source",
    "analysis",
    "revision",
    "revision_full",
    "receipt",
    "lifecycle",
    "closure",
    "projection",
    "investigation",
    "memory",
    "memory_seed",
    "field",
)
FAILURES = tuple(
    name for name in ORDER if name not in {"thread", "analysis", "memory", "memory_seed", "field"}
)


@dataclass
class ResearchCapture:
    fail_at: str | None = None
    events: list[str] = field(default_factory=list)
    calls: dict[str, Construction] = field(default_factory=dict)
    active: bool = False
    finished: bool = False

    def note(
        self, label: str, instance: object, args: tuple[object, ...], kwargs: dict[str, Any]
    ) -> None:
        if label == "thread":
            self.active = True
        if self.active and not self.finished:
            self.events.append(label)
            self.calls[label] = Construction(instance, args, dict(kwargs))
            if label == self.fail_at:
                raise RuntimeError(f"research construction failed: {label}")


def capture_research(
    monkeypatch: pytest.MonkeyPatch, fail_at: str | None = None
) -> ResearchCapture:
    captured = ResearchCapture(fail_at=fail_at)

    def install(label: str, cls: type[Any]) -> None:
        original = cast(Callable[..., None], cls.__init__)

        def construct(instance: object, *args: object, **kwargs: Any) -> None:
            captured.note(label, instance, args, kwargs)
            original(instance, *args, **kwargs)
            if label == "field":
                captured.finished = True

        monkeypatch.setattr(cls, "__init__", construct)

    constructors: dict[str, type[Any]] = {
        "thread": ThreadCommandHandlers,
        "loader": FilesystemProjectPackLoader,
        "pack_factory": ProjectPackExecutionFactory,
        "pack": ProjectPackCommandHandlers,
        "source": SourceCommandHandlers,
        "analysis": ThreadAnalysisCommandHandlers,
        "revision": RevisionCommandHandlers,
        "revision_full": RevisionFullHandlers,
        "receipt": ReceiptHandlers,
        "lifecycle": LifecycleCommandHandlers,
        "closure": ClosureHandlers,
        "projection": ProjectionQueryHandlers,
        "investigation": InvestigationQueryHandlers,
        "memory": MemoryFullHandlers,
        "field": FieldMeasurementHandlers,
    }
    for label, cls in constructors.items():
        install(label, cls)
    original_seed = MemoryFullHandlers.seed_policies

    def seed(handler: MemoryFullHandlers, project_id: str = "system:memory") -> None:
        captured.note("memory_seed", handler, (), {"project_id": project_id})
        original_seed(handler, project_id)

    monkeypatch.setattr(MemoryFullHandlers, "seed_policies", seed)
    return captured


@pytest.mark.parametrize("custom_pack", (False, True))
def test_domain_assembly_preserves_interleaving_and_scoped_consumers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, custom_pack: bool
) -> None:
    captured = capture_research(monkeypatch)
    factory = RecordingFactory()
    pack = tmp_path / "pack" if custom_pack else None
    runtime = create_runtime(
        tmp_path,
        projectpack_root=pack,
        storage_factory=factory,
        deployment_mode=DeploymentMode.LOCAL,
    )
    try:
        assert captured.events == list(ORDER)
        c = captured.calls
        assert c["loader"].args == ((pack if pack is not None else Path("examples/projectpacks")),)
        assert c["pack"].kwargs["loader"] is c["loader"].instance
        assert c["pack"].kwargs["executions"] is c["pack_factory"].instance
        assert c["pack_factory"].kwargs["storage_factory"] is factory
        assert c["pack_factory"].kwargs["workspace"] is tmp_path
        assert c["pack_factory"].kwargs["models"] is c["analysis"].kwargs["models"]
        assert c["source"].kwargs["inbox_root"] == tmp_path / "inbox"
        assert c["source"].kwargs["ledger"] is runtime.ledger
        assert c["source"].kwargs["projects"] is factory.bundles[0].projects
        assert c["source"].kwargs["artifacts"] is c["analysis"].kwargs["artifacts"]
        assert c["projection"].kwargs["artifacts"] is c["source"].kwargs["artifacts"]
        assert c["projection"].kwargs["research"] is c["analysis"].kwargs["research"]
        assert c["projection"].kwargs["ledger"] is runtime.ledger
        assert c["investigation"].kwargs["threads"] is c["thread"].kwargs["threads"]
        assert c["investigation"].kwargs["store"] is c["thread"].kwargs["investigations"]
        assert c["investigation"].kwargs["service"] is c["thread"].kwargs["investigation_service"]
        assert c["revision"].kwargs["ledger"] is c["revision_full"].kwargs["ledger"]
        assert isinstance(c["revision"].kwargs["ledger"], ScopedLedger)
        assert c["revision"].kwargs["ledger"] is not runtime.ledger
        for label in ("receipt", "lifecycle", "closure"):
            assert c[label].kwargs["unit_of_work"] is factory.bundles[0].immediate_uow
            assert c[label].kwargs["ledger"] is c["receipt"].kwargs["ledger"]
            assert isinstance(c[label].kwargs["ledger"], ScopedLedger)
        assert isinstance(c["receipt"].kwargs["records"], ScopedReceiptControls)
        assert c["closure"].kwargs["records"] is factory.bundles[0].controls
        assert c["memory_seed"].instance is c["memory"].instance
        assert c["memory_seed"].kwargs["project_id"] == "system:memory"
    finally:
        runtime.close()


@pytest.mark.parametrize("fail_at", FAILURES)
def test_domain_assembly_failure_retains_root_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail_at: str
) -> None:
    captured = capture_research(monkeypatch, fail_at)
    factory = RecordingFactory()
    closed: list[SqliteLedger] = []
    original = SqliteLedger.close

    def close(ledger: SqliteLedger) -> None:
        closed.append(ledger)
        original(ledger)

    monkeypatch.setattr(SqliteLedger, "close", close)
    with pytest.raises(RuntimeError, match=f"research construction failed: {fail_at}"):
        create_runtime(tmp_path, storage_factory=factory, deployment_mode=DeploymentMode.LOCAL)
    assert captured.events == list(ORDER[: ORDER.index(fail_at) + 1])
    assert closed == [factory.bundles[0].ledger]


async def test_unchanged_memory_seed_is_idempotent_and_publicly_consumed(tmp_path: Path) -> None:
    factory = RecordingFactory()
    runtime = create_runtime(
        tmp_path, storage_factory=factory, deployment_mode=DeploymentMode.LOCAL
    )
    try:
        rows = factory.bundles[0].controls.list(
            "system:memory", "MEMORY", "POLICY", latest_only=True
        )
        before = [row.model_dump(mode="json") for row in rows]
        assert len(before) == 2
        value(
            await runtime.bus.dispatch(
                request(
                    "project/create",
                    "project",
                    {
                        "project_id": "project:composition-memory",
                        "name": "Memory",
                        "cutoff_at": "2026-09-19T00:00:00Z",
                    },
                )
            )
        )
        policies = value(
            await runtime.bus.dispatch(
                request(
                    "memory/policy/list",
                    "policies",
                    {
                        "project_id": "project:composition-memory",
                    },
                )
            )
        )
        assert len(policies["policies"]) == 2
        policy = value(
            await runtime.bus.dispatch(
                request(
                    "memory/policy/read",
                    "policy",
                    {
                        "project_id": "project:composition-memory",
                        "profile_ref": "memory:assertion:1",
                    },
                )
            )
        )
        assert policy["policy"] in policies["policies"]
    finally:
        runtime.close()
    reopened = create_runtime(
        tmp_path, storage_factory=factory, deployment_mode=DeploymentMode.LOCAL
    )
    try:
        after = factory.bundles[1].controls.list(
            "system:memory", "MEMORY", "POLICY", latest_only=True
        )
        assert [row.model_dump(mode="json") for row in after] == before
    finally:
        reopened.close()
