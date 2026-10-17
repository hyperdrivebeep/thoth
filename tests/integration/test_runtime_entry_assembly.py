"""Preserve runtime entry wiring and distinguish product cleanup from test teardown."""

import json
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import pytest
from tests.atomicity.test_store_lifecycle import RecordingFactory
from tests.integration.test_runtime_acquisition_composition import Construction

from thoth.adapters.models import claude_code_login, claude_oauth, codex_broker, xai_broker
from thoth.adapters.models.catalog_startup import CatalogStartupRefresher
from thoth.adapters.storage.sqlite import SqliteLedger
from thoth.adapters.worker_identity import LocalWorkerIdentity
from thoth.application.commands.research_operations import ResearchOperationHandlers
from thoth.application.commands.research_queue_cancel import QueueAwareOperationCancel
from thoth.application.commands.research_threads import ResearchThreadHandlers
from thoth.apps.hosted_review_dispatch import wait_for_hosted_dispatch
from thoth.apps.judgment_review_composition import JudgmentReviewWiring
from thoth.apps.research_entry_composition import ResearchEntryComposition
from thoth.apps.runtime import create_runtime
from thoth.domain.deployment_mode import DeploymentMode
from thoth.protocol.bus import CommandBus
from thoth.protocol.registry import MethodRegistry

BROKERS = {
    "codex": codex_broker,
    "xai": xai_broker,
    "claude_oauth": claude_oauth,
    "claude_code": claude_code_login,
}


def broker_users() -> dict[str, dict[str, int]]:
    return {
        name: {
            str(key): value
            for key, value in cast(dict[Path, int], vars(module)["_BROKER_USERS"]).items()
        }
        for name, module in BROKERS.items()
    }


@dataclass
class EntryCapture:
    fail_at: str | None = None
    events: list[str] = field(default_factory=list)
    cleanup: list[str] = field(default_factory=list)
    calls: dict[str, Construction] = field(default_factory=dict)
    connection: tuple[JudgmentReviewWiring, CommandBus, ResearchThreadHandlers] | None = None
    bus_initialized: bool = False
    refresher_starts: int = 0


def capture_entry(monkeypatch: pytest.MonkeyPatch, fail_at: str | None = None) -> EntryCapture:
    captured = EntryCapture(fail_at=fail_at)

    def install(label: str, cls: type[Any]) -> None:
        original = cast(Callable[..., None], cls.__init__)

        def initialize(instance: object, *args: object, **kwargs: Any) -> None:
            captured.events.append(label)
            captured.calls[label] = Construction(instance, args, dict(kwargs))
            if label == fail_at:
                raise RuntimeError(f"entry assembly failed: {label}")
            original(instance, *args, **kwargs)
            if label == "bus":
                captured.bus_initialized = True

        monkeypatch.setattr(cls, "__init__", initialize)

    for label, cls in [
        ("entry", ResearchEntryComposition),
        ("bus", CommandBus),
        ("cancel", QueueAwareOperationCancel),
        ("operations", ResearchOperationHandlers),
    ]:
        install(label, cls)
    original_connect = JudgmentReviewWiring.connect

    def connect(
        wiring: JudgmentReviewWiring, bus: CommandBus, entry: ResearchThreadHandlers
    ) -> None:
        captured.events.append("connect")
        captured.connection = (wiring, bus, entry)
        if fail_at == "connect":
            raise RuntimeError("entry assembly failed: connect")
        original_connect(wiring, bus, entry)

    monkeypatch.setattr(JudgmentReviewWiring, "connect", connect)

    def cleanup(label: str, cls: type[Any], name: str = "close") -> None:
        original = cast(Callable[..., None], getattr(cls, name))

        def close(instance: object, *args: object, **kwargs: Any) -> None:
            captured.cleanup.append(label)
            original(instance, *args, **kwargs)

        monkeypatch.setattr(cls, name, close)

    for label, cls, method in [
        ("store", SqliteLedger, "close"),
        ("entry", ResearchEntryComposition, "close_storage"),
        ("worker", LocalWorkerIdentity, "close"),
        ("refresher", CatalogStartupRefresher, "close"),
        ("bus_tasks", CommandBus, "close_tasks"),
        ("codex", codex_broker.CodexAuthBroker, "close"),
        ("xai", xai_broker.XaiAuthBroker, "close"),
        ("claude_oauth", claude_oauth.ClaudeOAuthBroker, "close"),
        ("claude_code", claude_code_login.ClaudeCodeLoginBroker, "close"),
    ]:
        cleanup(label, cls, method)

    def controlled_start(_refresher: CatalogStartupRefresher) -> None:
        # Observe startup intent without probing an actual account or transport.
        captured.refresher_starts += 1

    monkeypatch.setattr(CatalogStartupRefresher, "start", controlled_start)
    return captured


@pytest.mark.parametrize("mode", (DeploymentMode.LOCAL, DeploymentMode.HOSTED_REVIEW))
def test_entry_bus_identity_cancel_controls_and_normal_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: DeploymentMode
) -> None:
    before = broker_users()
    captured = capture_entry(monkeypatch)
    runtime = create_runtime(tmp_path, deployment_mode=mode)
    composition = cast(ResearchEntryComposition, captured.calls["entry"].instance)
    try:
        assert captured.events == ["entry", "bus", "connect", "cancel", "operations"]
        assert getattr(runtime.close_storage, "__self__", None) is composition
        bus = captured.calls["bus"]
        assert bus.instance is runtime.bus
        assert bus.kwargs["queued_admission"] == composition.entry.queued_admission
        assert bus.kwargs["seal_abandoned_running"] is (mode is DeploymentMode.HOSTED_REVIEW)
        assert bus.kwargs["before_continue"] is (
            wait_for_hosted_dispatch if mode is DeploymentMode.HOSTED_REVIEW else None
        )
        assert captured.connection is not None
        assert captured.connection[1:] == (runtime.bus, composition.entry)
        cancel = cast(QueueAwareOperationCancel, captured.calls["cancel"].instance)
        registry = cast(MethodRegistry, bus.args[0])
        assert getattr(registry.resolve("operation/cancel"), "__self__", None) is cancel
        assert (
            cancel.records is composition.entry.records and cancel.queue is composition.entry.queue
        )
        operation_owner = getattr(cancel.original, "__self__", None)
        assert getattr(cancel.original, "__name__", None) == "cancel"
        operations = cast(ResearchOperationHandlers, captured.calls["operations"].instance)
        assert operations.host is composition.entry
        assert getattr(operations.legacy_pause, "__self__", None) is operation_owner
        assert getattr(operations.legacy_resume, "__self__", None) is operation_owner
        assert getattr(registry.resolve("operation/pause"), "__self__", None) is operations
        assert getattr(registry.resolve("operation/resume"), "__self__", None) is operations
        assert captured.cleanup == []
    finally:
        runtime.close()
    expected = ["bus_tasks", "entry"]
    if composition.catalog_refresher is not None:
        expected.append("refresher")
    expected.extend(["worker", "store"])
    if mode is DeploymentMode.LOCAL:
        expected.extend(["claude_code", "claude_oauth", "xai", "codex"])
    assert captured.cleanup == expected
    assert broker_users() == before


@pytest.mark.parametrize("mode", (DeploymentMode.LOCAL, DeploymentMode.HOSTED_REVIEW))
@pytest.mark.parametrize("fail_at", ("bus", "connect", "cancel", "operations"))
def test_post_entry_failure_releases_resources_before_test_teardown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    record_property: Callable[[str, object], None],
    fail_at: str,
    mode: DeploymentMode,
) -> None:
    # RT-LEAK-1 D contract replaces the step28 observation of leaked resources.
    before = broker_users()
    workers_before = set(cast(set[str], vars(LocalWorkerIdentity)["_active"]))
    captured = capture_entry(monkeypatch, fail_at)
    factory = RecordingFactory()
    try:
        with pytest.raises(RuntimeError, match=f"entry assembly failed: {fail_at}"):
            create_runtime(tmp_path, storage_factory=factory, deployment_mode=mode)
        expected_events = ["entry", "bus", "connect", "cancel", "operations"]
        assert captured.events == expected_events[: expected_events.index(fail_at) + 1]
        product_cleanup = [label for label in captured.cleanup if label != "entry"]
        record_property("product_cleanup", json.dumps(dict(Counter(product_cleanup))))
        record_property("product_broker_users_restored", broker_users() == before)
        expected_cleanup = [] if fail_at == "bus" else ["bus_tasks"]
        composition = cast(ResearchEntryComposition, captured.calls["entry"].instance)
        if composition.catalog_refresher is not None:
            expected_cleanup.append("refresher")
        expected_cleanup.append("worker")
        if mode is DeploymentMode.LOCAL:
            expected_cleanup.extend(["claude_code", "claude_oauth", "xai", "codex"])
        expected_cleanup.append("store")
        assert product_cleanup == expected_cleanup
        assert broker_users() == before
        assert cast(set[str], vars(LocalWorkerIdentity)["_active"]) == workers_before
    finally:
        # Rescue only an unfixed failure; observations above are product evidence.
        boundary = len(captured.cleanup)
        if "worker" not in captured.cleanup and "entry" in captured.calls:
            if captured.bus_initialized and "bus_tasks" not in captured.cleanup:
                cast(CommandBus, captured.calls["bus"].instance).close_tasks()
            cast(ResearchEntryComposition, captured.calls["entry"].instance).close_storage()
        record_property(
            "test_teardown_cleanup", json.dumps(dict(Counter(captured.cleanup[boundary:])))
        )
        record_property("refresher_start_controlled_no_network", True)
    assert broker_users() == before
