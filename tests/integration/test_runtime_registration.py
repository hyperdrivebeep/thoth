"""Preserve startup registration order and failure cleanup across route extraction."""

import inspect
import json
from collections.abc import Callable
from pathlib import Path

import pytest
from tests.atomicity.test_store_lifecycle import RecordingFactory

from thoth.adapters.storage.sqlite import SqliteLedger
from thoth.apps.runtime import create_runtime
from thoth.domain.deployment_mode import DeploymentMode
from thoth.protocol.registry import CommandHandler, MethodRegistry

FIXTURE = Path(__file__).parents[1] / "fixtures/runtime_registration_events.json"


def capture_registration(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    events: list[list[str]] = []
    original_register = MethodRegistry.register
    original_decorate = MethodRegistry.decorate

    def record(kind: str, method: str, handler: CommandHandler) -> None:
        unwrapped = inspect.unwrap(handler)
        events.append([kind, method, unwrapped.__module__ + "." + unwrapped.__qualname__])

    def register(registry: MethodRegistry, method: str, handler: CommandHandler) -> None:
        original_register(registry, method, handler)
        record("register", method, registry.resolve(method))

    def decorate(
        registry: MethodRegistry,
        method: str,
        decorator: Callable[[CommandHandler], CommandHandler],
    ) -> None:
        original_decorate(registry, method, decorator)
        record("decorate", method, registry.resolve(method))

    monkeypatch.setattr(MethodRegistry, "register", register)
    monkeypatch.setattr(MethodRegistry, "decorate", decorate)
    return events


@pytest.mark.parametrize("mode", [DeploymentMode.LOCAL, DeploymentMode.HOSTED_REVIEW])
def test_registration_and_decoration_order_preserves_original_callables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: DeploymentMode
) -> None:
    events = capture_registration(monkeypatch)
    runtime = create_runtime(tmp_path, deployment_mode=mode)
    try:
        expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
        assert events == expected[mode.value]
    finally:
        runtime.close()


@pytest.mark.parametrize(
    "failed_method", ["closure/prepare", "project/source/connect", "criteria/list", "object/list"]
)
def test_registration_failure_closes_open_store_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed_method: str
) -> None:
    factory = RecordingFactory()
    closed: list[SqliteLedger] = []
    original_close = SqliteLedger.close
    original_register = MethodRegistry.register

    def close(ledger: SqliteLedger) -> None:
        closed.append(ledger)
        original_close(ledger)

    def register(registry: MethodRegistry, method: str, handler: CommandHandler) -> None:
        original_register(registry, method, handler)
        if method == failed_method:
            raise RuntimeError("registration failed")

    monkeypatch.setattr(SqliteLedger, "close", close)
    monkeypatch.setattr(MethodRegistry, "register", register)
    with pytest.raises(RuntimeError, match="registration failed"):
        create_runtime(tmp_path, storage_factory=factory, deployment_mode=DeploymentMode.LOCAL)
    assert len(factory.bundles) == 1
    assert closed == [factory.bundles[0].ledger]
