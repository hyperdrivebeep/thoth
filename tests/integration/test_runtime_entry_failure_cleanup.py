"""RT-LEAK-1: failed startup returns its resources without harming another owner."""

from pathlib import Path
from typing import Any, cast

import pytest
from tests.integration.test_runtime_entry_assembly import (
    BROKERS,
    broker_users,
    capture_entry,
)

from thoth.adapters.models.catalog_startup import CatalogStartupRefresher
from thoth.adapters.worker_identity import LocalWorkerIdentity
from thoth.application.commands.research_threads import ResearchThreadHandlers
from thoth.apps.judgment_review_composition import JudgmentReviewWiring
from thoth.apps.research_entry_composition import ResearchEntryComposition
from thoth.apps.runtime import create_runtime
from thoth.domain.deployment_mode import DeploymentMode
from thoth.protocol.bus import CommandBus
from thoth.protocol.registry import MethodRegistry


@pytest.mark.parametrize("cause_type", (RuntimeError, KeyboardInterrupt))
@pytest.mark.parametrize("fail_at", ("bus", "connect", "decorate", "operations"))
def test_original_exception_identity_and_failed_startup_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cause_type: type[BaseException],
    fail_at: str,
) -> None:
    before = broker_users()
    workers = set(cast(set[str], vars(LocalWorkerIdentity)["_active"]))
    captured = capture_entry(monkeypatch)
    cause = cause_type("synthetic startup failure")

    def fail(*_args: Any, **_kwargs: Any) -> None:
        raise cause

    if fail_at == "bus":
        monkeypatch.setattr(CommandBus, "__init__", fail)
    elif fail_at == "connect":
        monkeypatch.setattr(JudgmentReviewWiring, "connect", fail)
    else:
        original = MethodRegistry.decorate if fail_at == "decorate" else MethodRegistry.register

        def fail_registration(registry: MethodRegistry, method: str, handler: Any) -> None:
            if method == (
                "operation/cancel" if fail_at == "decorate" else "research/operation/read"
            ):
                raise cause
            original(registry, method, handler)

        # Research operation registration decorates pause/resume, not a new RPC.
        if fail_at == "operations":
            from thoth.application.commands.research_operations import ResearchOperationHandlers

            monkeypatch.setattr(ResearchOperationHandlers, "__init__", fail)
        else:
            monkeypatch.setattr(MethodRegistry, "decorate", fail_registration)
    try:
        with pytest.raises(cause_type) as raised:
            create_runtime(tmp_path, deployment_mode=DeploymentMode.LOCAL)
        assert raised.value is cause
        assert broker_users() == before
        assert cast(set[str], vars(LocalWorkerIdentity)["_active"]) == workers
        assert "entry" not in captured.cleanup  # Root owns store cleanup on startup failure.
        assert captured.cleanup.count("store") == 1
    finally:
        if (
            cast(set[str], vars(LocalWorkerIdentity)["_active"]) != workers
            and "entry" in captured.calls
        ):
            # Red-run rescue only; never counts as product cleanup.
            cast(ResearchEntryComposition, captured.calls["entry"].instance).close_storage()


@pytest.mark.parametrize("cleanup_failure", ("bus", "refresher", "worker", "claude_code"))
def test_cleanup_error_does_not_mask_startup_or_skip_other_resources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cleanup_failure: str
) -> None:
    before = broker_users()
    workers = set(cast(set[str], vars(LocalWorkerIdentity)["_active"]))
    captured = capture_entry(monkeypatch)
    cause = RuntimeError("synthetic startup failure")

    def fail_connect(
        _wiring: JudgmentReviewWiring, _bus: CommandBus, _entry: ResearchThreadHandlers
    ) -> None:
        raise cause

    monkeypatch.setattr(JudgmentReviewWiring, "connect", fail_connect)
    cls, name = {
        "bus": (CommandBus, "close_tasks"),
        "refresher": (CatalogStartupRefresher, "close"),
        "worker": (LocalWorkerIdentity, "close"),
        "claude_code": (BROKERS["claude_code"].ClaudeCodeLoginBroker, "close"),
    }[cleanup_failure]
    original = getattr(cls, name)

    def close_then_fail(instance: Any) -> None:
        original(instance)
        raise OSError("synthetic-sensitive-cleanup-details")

    monkeypatch.setattr(cls, name, close_then_fail)
    with pytest.raises(RuntimeError) as raised:
        create_runtime(tmp_path, deployment_mode=DeploymentMode.LOCAL)
    assert raised.value is cause
    notes = getattr(cause, "__notes__", [])
    assert len(notes) == 1 and "startup cleanup failed" in notes[0]
    assert "OSError" in notes[0]
    assert "synthetic-sensitive" not in notes[0]
    assert captured.cleanup == [
        "bus_tasks",
        "refresher",
        "worker",
        "claude_code",
        "claude_oauth",
        "xai",
        "codex",
        "store",
    ]
    assert broker_users() == before
    assert cast(set[str], vars(LocalWorkerIdentity)["_active"]) == workers


def test_failed_second_runtime_preserves_first_owner_and_can_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = broker_users()
    workers = set(cast(set[str], vars(LocalWorkerIdentity)["_active"]))
    capture_entry(monkeypatch)
    first = create_runtime(tmp_path, deployment_mode=DeploymentMode.LOCAL)
    try:
        owned = broker_users()
        owned_workers = set(cast(set[str], vars(LocalWorkerIdentity)["_active"]))
        broker_objects = {
            name: module.broker_for_workspace(tmp_path) for name, module in BROKERS.items()
        }
        with monkeypatch.context() as failed:

            def fail_connect(*_args: Any, **_kwargs: Any) -> None:
                raise RuntimeError("second startup failed")

            failed.setattr(JudgmentReviewWiring, "connect", fail_connect)
            with pytest.raises(RuntimeError, match="second startup failed"):
                create_runtime(tmp_path, deployment_mode=DeploymentMode.LOCAL)
        assert broker_users() == owned
        assert cast(set[str], vars(LocalWorkerIdentity)["_active"]) == owned_workers
        assert all(
            module.broker_for_workspace(tmp_path) is broker_objects[name]
            for name, module in BROKERS.items()
        )
        assert first.ledger.engine.connect().close() is None
        retry = create_runtime(tmp_path, deployment_mode=DeploymentMode.LOCAL)
        retry.close()
        assert broker_users() == owned
    finally:
        first.close()
    assert broker_users() == before
    assert cast(set[str], vars(LocalWorkerIdentity)["_active"]) == workers
