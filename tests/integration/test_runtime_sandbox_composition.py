"""Preserve sandbox selection, construction order and canonical execution ownership."""

from collections.abc import Callable
from dataclasses import dataclass, field
from inspect import getattr_static
from pathlib import Path
from typing import Any, cast

import pytest
from tests.atomicity.test_store_lifecycle import RecordingFactory
from tests.integration.test_runtime_acquisition_composition import Construction

from thoth.adapters.sandbox import DisabledSandboxAdapter
from thoth.adapters.storage.sqlite import SqliteLedger
from thoth.application.commands.executions_full import ExecutionHandlers
from thoth.application.commands.projects import ProjectCommandHandlers
from thoth.application.services.execution_service import ExecutionService
from thoth.application.services.policy_gate import PolicyGate
from thoth.application.services.r2_closed_loop import R2ClosedLoopCoordinator
from thoth.application.services.r2_sandbox_compiler import R2SandboxSpecCompiler
from thoth.application.services.r2_test_lifecycle import R2TestLifecycle
from thoth.application.services.sandbox_router import RegisteredSandboxRouter
from thoth.application.services.sandbox_service import SandboxService
from thoth.apps.runtime import create_runtime
from thoth.domain.deployment_mode import DeploymentMode
from thoth.domain.sandbox import SandboxResult, SandboxRunSpec, SandboxRuntimeProfile
from thoth.domain.sandbox_capability import SandboxCapability

ORDER = (
    "disabled",
    "router",
    "capability",
    "register",
    "policy",
    "sandbox",
    "execution",
    "compiler",
    "tests",
    "r2",
)


@dataclass
class SandboxCapture:
    fail_at: str | None = None
    events: list[str] = field(default_factory=list)
    calls: dict[str, Construction] = field(default_factory=dict)
    active: bool = False
    finished: bool = False

    def note(self, label: str) -> None:
        if label in {"disabled", "choose"}:
            self.active = True
        if self.active and not self.finished:
            self.events.append(label)
            if label == self.fail_at:
                raise RuntimeError(f"sandbox construction failed: {label}")


class ProbeSandbox:
    def __init__(self, capture: SandboxCapture, *, truthy: bool, available: bool) -> None:
        self.capture = capture
        self.truthy = truthy
        self.declared = SandboxCapability(
            runtime_profile=SandboxRuntimeProfile.SCRIPTED,
            available=available,
            runtime_version="probe:7",
            security_tier="TEST_ONLY",
            network_modes=("DENY_ALL",),
        )

    def __bool__(self) -> bool:
        self.capture.note("choose")
        return self.truthy

    @property
    def capability(self) -> SandboxCapability:
        self.capture.note("capability")
        return self.declared

    async def run(self, spec: SandboxRunSpec) -> SandboxResult:
        raise AssertionError("composition must not run a sandbox")

    async def cancel(self, attempt_id: str) -> bool:
        raise AssertionError("composition must not cancel a sandbox")


def capture_sandbox(monkeypatch: pytest.MonkeyPatch, fail_at: str | None = None) -> SandboxCapture:
    captured = SandboxCapture(fail_at=fail_at)

    def install(label: str, cls: type[Any]) -> None:
        original = cast(Callable[..., None], cls.__init__)

        def construct(instance: object, *args: object, **kwargs: Any) -> None:
            captured.note(label)
            if (captured.active and not captured.finished) or label in {"handler", "project"}:
                captured.calls[label] = Construction(instance, args, dict(kwargs))
            original(instance, *args, **kwargs)
            if label == "r2":
                captured.finished = True

        monkeypatch.setattr(cls, "__init__", construct)

    constructors: dict[str, type[Any]] = {
        "disabled": DisabledSandboxAdapter,
        "router": RegisteredSandboxRouter,
        "policy": PolicyGate,
        "sandbox": SandboxService,
        "execution": ExecutionService,
        "compiler": R2SandboxSpecCompiler,
        "tests": R2TestLifecycle,
        "r2": R2ClosedLoopCoordinator,
        "handler": ExecutionHandlers,
        "project": ProjectCommandHandlers,
    }
    for label, cls in constructors.items():
        install(label, cls)
    original_register = RegisteredSandboxRouter.register

    def register(router: RegisteredSandboxRouter, *args: Any, **kwargs: Any) -> None:
        captured.note("register")
        captured.calls["register"] = Construction(router, args, dict(kwargs))
        original_register(router, *args, **kwargs)

    monkeypatch.setattr(RegisteredSandboxRouter, "register", register)
    original_property = cast(property, getattr_static(DisabledSandboxAdapter, "capability"))
    getter = cast(Callable[[DisabledSandboxAdapter], SandboxCapability], original_property.fget)

    def capability(adapter: DisabledSandboxAdapter) -> SandboxCapability:
        captured.note("capability")
        return getter(adapter)

    monkeypatch.setattr(DisabledSandboxAdapter, "capability", property(capability))
    return captured


@pytest.mark.parametrize("kind", ("default", "scripted", "unavailable", "falsy"))
def test_runtime_sandbox_selection_order_and_execution_owners(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    captured = capture_sandbox(monkeypatch)
    supplied = (
        None
        if kind == "default"
        else ProbeSandbox(captured, truthy=kind != "falsy", available=kind != "unavailable")
    )
    factory = RecordingFactory()
    runtime = create_runtime(
        tmp_path,
        sandbox_adapter=supplied,
        storage_factory=factory,
        deployment_mode=DeploymentMode.LOCAL,
    )
    try:
        expected = list(ORDER)
        if kind != "default":
            expected = ["choose"] + (expected if kind == "falsy" else expected[1:])
        assert captured.events == expected
        calls = captured.calls
        registered = calls["register"]
        effective = registered.args[1]
        assert registered.args[0] == SandboxRuntimeProfile.SCRIPTED
        fallback = kind in {"default", "falsy"}
        if fallback:
            assert effective is calls["disabled"].instance
            assert registered.kwargs == {
                "available": False,
                "version": "disabled:1",
                "security_tier": "UNAVAILABLE",
                "network_modes": ("DENY_ALL",),
            }
        else:
            assert effective is supplied
            assert registered.kwargs == {
                "available": kind != "unavailable",
                "version": "probe:7",
                "security_tier": "TEST_ONLY",
                "network_modes": ("DENY_ALL",),
            }
        router = cast(RegisteredSandboxRouter, calls["router"].instance)
        if kind == "scripted":
            selected, capability = router.resolve(SandboxRuntimeProfile.SCRIPTED)
            assert supplied is not None
            assert selected is supplied and capability == supplied.declared
        else:
            with pytest.raises(ValueError, match="sandbox runtime capability is unavailable"):
                router.resolve(SandboxRuntimeProfile.SCRIPTED)
        sandbox = calls["sandbox"]
        execution = calls["execution"]
        assert sandbox.kwargs["router"] is router
        assert sandbox.kwargs["policies"] is calls["policy"].instance
        assert calls["r2"].kwargs["sandbox"] is sandbox.instance
        assert calls["tests"].kwargs["sandbox"] is sandbox.instance
        assert calls["handler"].kwargs["sandbox"] is sandbox.instance
        assert calls["handler"].kwargs["service"] is execution.instance
        assert calls["tests"].kwargs["execution_service"] is execution.instance
        bundle = factory.bundles[0]
        assert execution.kwargs["store"] is bundle.executions
        assert calls["tests"].kwargs["executions"] is calls["handler"].kwargs["store"]
        assert calls["tests"].kwargs["executions"] is not bundle.executions
        for name in ("ledger", "clock", "ids"):
            assert sandbox.kwargs[name] is execution.kwargs[name] is calls["r2"].kwargs[name]
        assert sandbox.kwargs["artifacts"] is execution.kwargs["artifacts"]
        assert sandbox.kwargs["ledger"] is bundle.ledger
        assert calls["policy"].kwargs["policies"] is bundle.governance
        policy = calls["project"].kwargs["default_policy_payload"]
        assert policy["sandbox_runtime_allowlist"] == (
            []
            if supplied is None
            else ["SCRIPTED", "DOCKER_POC", "GVISOR", "FIRECRACKER", "MANAGED"]
        )
    finally:
        runtime.close()


@pytest.mark.parametrize("fail_at", ORDER)
def test_sandbox_composition_failure_preserves_root_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail_at: str
) -> None:
    captured = capture_sandbox(monkeypatch, fail_at)
    factory = RecordingFactory()
    closed: list[SqliteLedger] = []
    original_close = SqliteLedger.close

    def close(ledger: SqliteLedger) -> None:
        closed.append(ledger)
        original_close(ledger)

    monkeypatch.setattr(SqliteLedger, "close", close)
    with pytest.raises(RuntimeError, match=f"sandbox construction failed: {fail_at}"):
        create_runtime(tmp_path, storage_factory=factory, deployment_mode=DeploymentMode.LOCAL)
    assert captured.events == list(ORDER[: ORDER.index(fail_at) + 1])
    assert closed == [factory.bundles[0].ledger]
