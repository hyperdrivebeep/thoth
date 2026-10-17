"""Location-independent characterization of runtime acquisition construction."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from tests.atomicity.test_store_lifecycle import RecordingFactory
from tests.integration.storage_coverage_helpers import request, value

from thoth.adapters.connectors import ConnectorRegistry, GitReadConnector, LocalFileConnector
from thoth.adapters.connectors.project_public_web import ProjectPublicWebConnector
from thoth.adapters.storage.acquisition import SqliteAcquisitionUnitOfWork
from thoth.adapters.storage.sqlite import SqliteLedger
from thoth.adapters.storage.workspace_setup import write_setup
from thoth.application.commands.projects import ProjectCommandHandlers
from thoth.application.services.acquisition_coordinator import AcquisitionCoordinator
from thoth.application.services.connector_service import ConnectorService
from thoth.application.services.critical_counter_search_coordinator import (
    CriticalCounterSearchCoordinator,
)
from thoth.application.services.policy_gate import PolicyGate
from thoth.apps.runtime import create_runtime
from thoth.domain.connectors import ConnectorAccessRequest, ConnectorFailure
from thoth.domain.deployment_mode import DeploymentMode
from thoth.domain.enums import AuthorityState, CutoffState, SecurityClass
from thoth.domain.web_acquisition import WebPage
from thoth.domain.workspace_setup import WorkspaceSetupState

ORDER = (
    "web",
    "local",
    "git",
    "registry",
    "policy",
    "uow",
    "connector",
    "acquisition",
    "counter",
)
CONSTRUCTORS: dict[str, type[Any]] = {
    "web": ProjectPublicWebConnector,
    "local": LocalFileConnector,
    "git": GitReadConnector,
    "registry": ConnectorRegistry,
    "policy": PolicyGate,
    "uow": SqliteAcquisitionUnitOfWork,
    "connector": ConnectorService,
    "acquisition": AcquisitionCoordinator,
    "counter": CriticalCounterSearchCoordinator,
    "project": ProjectCommandHandlers,
}


@dataclass
class Construction:
    instance: object
    args: tuple[object, ...]
    kwargs: dict[str, Any]


@dataclass
class Capture:
    events: list[str] = field(default_factory=list)
    calls: dict[str, Construction] = field(default_factory=dict)
    active: bool = False
    finished: bool = False


class DiscoveryReader:
    def __init__(self) -> None:
        self.uris: list[str] = []

    async def read(self, uri: str, *, max_bytes: int, timeout: float) -> WebPage:
        del max_bytes, timeout
        self.uris.append(uri)
        return WebPage(
            requested_uri=uri,
            final_uri=uri,
            media_type="text/html",
            raw=b'<a href="/abs/2609.00001">paper</a><a href="https://evil.example/x">bad</a>',
            retrieved_at=datetime(2026, 9, 20, tzinfo=UTC),
        )


def discovery_request() -> ConnectorAccessRequest:
    return ConnectorAccessRequest(
        actor_id="agent:test",
        project_id="project:test",
        connector_id="project-public-web",
        selector={"mode": "SITE_DISCOVER", "entrypoint_id": "arxiv:cs.LG:recent"},
        authority=AuthorityState.OFFICIAL,
        cutoff_state=CutoffState.ELIGIBLE,
        security_class=SecurityClass.PUBLIC,
        cutoff_at=datetime(2026, 9, 20, tzinfo=UTC),
        policy_id="policy:test:v1",
        policy_revision=1,
        policy_digest="a" * 64,
    )


def capture_construction(
    monkeypatch: pytest.MonkeyPatch,
    *,
    fail_at: str | None = None,
    reader: DiscoveryReader | None = None,
    reader_hosts: list[tuple[str, ...]] | None = None,
) -> Capture:
    captured = Capture()

    def install(label: str, cls: type[Any]) -> None:
        original = cast(Callable[..., None], cls.__init__)

        def construct(instance: object, *args: object, **kwargs: Any) -> None:
            if label == "web":
                captured.active = True
            observing = captured.active and not captured.finished
            if observing or label == "project":
                captured.calls[label] = Construction(instance, args, dict(kwargs))
            if observing:
                captured.events.append(label)
                if label == fail_at:
                    raise RuntimeError(f"acquisition construction failed: {label}")
            if label == "web" and reader is not None:
                selected_reader = reader

                def factory(hosts: tuple[str, ...]) -> DiscoveryReader:
                    if reader_hosts is not None:
                        reader_hosts.append(hosts)
                    return selected_reader

                kwargs["reader_factory"] = factory
            original(instance, *args, **kwargs)
            if label == "counter":
                captured.finished = True

        # Patch the class object, never its import binding in the composition module.
        monkeypatch.setattr(cls, "__init__", construct)

    for label, cls in CONSTRUCTORS.items():
        install(label, cls)
    return captured


@pytest.mark.parametrize("registry_kind", ("default", "empty", "local"))
def test_runtime_acquisition_order_and_shared_dependencies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, registry_kind: str
) -> None:
    supplied = (
        None
        if registry_kind == "default"
        else ConnectorRegistry(
            () if registry_kind == "empty" else (LocalFileConnector(tmp_path / "inbox"),)
        )
    )
    captured = capture_construction(monkeypatch)
    factory = RecordingFactory()

    def fault(_stage: str) -> None:
        raise AssertionError("constructing a runtime must not commit acquisition")

    runtime = create_runtime(
        tmp_path,
        connector_registry=supplied,
        storage_factory=factory,
        acquisition_fault_injector=fault,
        deployment_mode=DeploymentMode.LOCAL,
    )
    try:
        expected = (
            ORDER
            if supplied is None
            else tuple(item for item in ORDER if item not in {"local", "git", "registry"})
        )
        assert captured.events == list(expected)
        calls = captured.calls
        connector = calls["connector"]
        if supplied is None:
            registry = cast(ConnectorRegistry, calls["registry"].instance)
            assert calls["registry"].args == (
                (calls["local"].instance, calls["git"].instance, calls["web"].instance),
            )
            assert [item.connector_id for item in registry.capabilities()] == [
                "local-file-upload",
                "project-public-web",
                "read-only-git-snapshot",
            ]
        else:
            registry = supplied
        assert connector.kwargs["registry"] is registry
        acquisition, counter = calls["acquisition"], calls["counter"]
        assert acquisition.kwargs["connectors"] is connector.instance
        assert counter.kwargs["connectors"] is connector.instance
        for name in ("evidence_graph", "evidence_unit_of_work", "investigations", "traces"):
            assert acquisition.kwargs[name] is counter.kwargs[name]
        for name in ("clock", "ids"):
            assert connector.kwargs[name] is acquisition.kwargs[name] is counter.kwargs[name]
        bundle = factory.bundles[0]
        assert counter.kwargs["ledger"] is runtime.ledger is bundle.ledger
        assert counter.kwargs["governance"] is counter.kwargs["policies"] is bundle.governance
        assert acquisition.kwargs["policies"] is bundle.governance
        assert calls["policy"].kwargs["policies"] is bundle.governance
        assert connector.kwargs["policies"] is calls["policy"].instance
        assert connector.kwargs["projects"] is bundle.projects
        assert connector.kwargs["unit_of_work"] is calls["uow"].instance
        assert calls["uow"].args == (bundle.ledger.engine,)
        assert calls["uow"].kwargs["fault_injector"] is fault
        assert calls["uow"].kwargs["resource_scopes"] is counter.kwargs["artifacts"].scopes
        callback = cast(Callable[[], WorkspaceSetupState], connector.kwargs["workspace_setup"])
        assert callback is calls["project"].kwargs["workspace_setup"]
        assert callback().storage_status == "MISSING"
        first = write_setup(WorkspaceSetupState(internet_consent="ALLOWED"), tmp_path)
        assert callback() == first
        second = write_setup(WorkspaceSetupState(internet_consent="DENIED"), tmp_path)
        assert callback() == second
    finally:
        runtime.close()


@pytest.mark.parametrize("fail_at", ORDER)
def test_acquisition_constructor_failure_closes_store_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail_at: str
) -> None:
    captured = capture_construction(monkeypatch, fail_at=fail_at)
    factory = RecordingFactory()
    closed: list[SqliteLedger] = []
    original_close = SqliteLedger.close

    def close(ledger: SqliteLedger) -> None:
        closed.append(ledger)
        original_close(ledger)

    monkeypatch.setattr(SqliteLedger, "close", close)
    with pytest.raises(RuntimeError, match=f"acquisition construction failed: {fail_at}"):
        create_runtime(tmp_path, storage_factory=factory, deployment_mode=DeploymentMode.LOCAL)
    assert captured.events == list(ORDER[: ORDER.index(fail_at) + 1])
    assert closed == [factory.bundles[0].ledger]


async def test_managed_connector_reads_policy_lazily_through_public_discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reader = DiscoveryReader()
    hosts: list[tuple[str, ...]] = []
    captured = capture_construction(monkeypatch, reader=reader, reader_hosts=hosts)
    write_setup(WorkspaceSetupState(internet_consent="ALLOWED"), tmp_path)
    runtime = create_runtime(tmp_path, deployment_mode=DeploymentMode.LOCAL)
    try:
        web = cast(ProjectPublicWebConnector, captured.calls["web"].instance)
        discovery = discovery_request()
        with pytest.raises(ConnectorFailure, match="SITE_DISCOVERY_UNSUPPORTED"):
            await web.discover(discovery)
        assert reader.uris == []
        created = value(
            await runtime.bus.dispatch(
                request(
                    "project/create",
                    "create",
                    {
                        "project_id": "project:test",
                        "name": "Lazy hosts",
                        "cutoff_at": "2026-09-19T00:00:00Z",
                    },
                )
            )
        )
        enabled = value(
            await runtime.bus.dispatch(
                request(
                    "project/policy/update",
                    "allow",
                    {
                        "project_id": "project:test",
                        "expected_revision": created["revision"],
                        "payload": {
                            "public_web": {"enabled": True, "preferred_hosts": ["arxiv.org"]}
                        },
                    },
                )
            )
        )
        refs = await web.discover(discovery)
        assert [ref.source_uri for ref in refs] == ["https://arxiv.org/abs/2609.00001"]
        assert hosts == [("arxiv.org",)]
        value(
            await runtime.bus.dispatch(
                request(
                    "project/policy/update",
                    "replace-host",
                    {
                        "project_id": "project:test",
                        "expected_revision": enabled["revision"],
                        "payload": {
                            "public_web": {"enabled": True, "preferred_hosts": ["example.org"]}
                        },
                    },
                )
            )
        )
        with pytest.raises(ConnectorFailure, match="SITE_DISCOVERY_UNSUPPORTED"):
            await web.discover(discovery)
        assert reader.uris == ["https://arxiv.org/list/cs.LG/recent"]
    finally:
        runtime.close()
