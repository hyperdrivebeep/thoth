from pathlib import Path

import pytest
from tests.integration.scoped_runtime import fixture_scope_policy
from tests.integration.storage_coverage_helpers import request, value

from thoth.adapters.storage.bundle import SqliteStoreBundle, SqliteStoreFactory
from thoth.adapters.storage.registry import StoreFactoryRegistry
from thoth.adapters.storage.sqlite import SqliteLedger
from thoth.apps.runtime import create_runtime
from thoth.apps.storage_composition import open_registered_stores


class RecordingFactory:
    provider_id = "atomicity-test-store"
    version = "1.0.0"

    def __init__(self) -> None:
        self.bundles: list[SqliteStoreBundle] = []

    def open(self, workspace: Path) -> SqliteStoreBundle:
        bundle = SqliteStoreFactory().open(workspace)
        self.bundles.append(bundle)
        return bundle


def test_registry_rejects_duplicate_and_unknown_before_open(tmp_path: Path) -> None:
    factory = RecordingFactory()
    registry = StoreFactoryRegistry((factory,))
    with pytest.raises(ValueError, match="ALREADY_REGISTERED"):
        registry.register(factory)
    with pytest.raises(ValueError, match="NOT_REGISTERED"):
        open_registered_stores(tmp_path, registry, factory.provider_id, "unknown")
    assert factory.bundles == []


def test_partial_bundle_failure_closes_engine_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    closed: list[SqliteLedger] = []
    original = SqliteLedger.close

    def close(ledger: SqliteLedger) -> None:
        closed.append(ledger)
        original(ledger)

    def fail(bundle: SqliteStoreBundle, ledger: SqliteLedger, workspace: Path) -> None:
        raise RuntimeError("bundle construction failed")

    monkeypatch.setattr(SqliteLedger, "close", close)
    monkeypatch.setattr(SqliteStoreBundle, "__init__", fail)
    with pytest.raises(RuntimeError, match="bundle construction failed"):
        SqliteStoreFactory().open(tmp_path)
    assert len(closed) == 1


async def test_injected_bundle_project_source_thread_reopen_and_close_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory = RecordingFactory()
    closed: list[SqliteLedger] = []
    original = SqliteLedger.close

    def close(ledger: SqliteLedger) -> None:
        closed.append(ledger)
        original(ledger)

    monkeypatch.setattr(SqliteLedger, "close", close)
    runtime = create_runtime(
        tmp_path, storage_factory=factory, resource_scope_policy=fixture_scope_policy()
    )
    project, thread = "project:bundle-uow", "thread:bundle-uow"
    try:
        value(
            await runtime.bus.dispatch(
                request(
                    "project/create",
                    "create",
                    {"project_id": project, "name": "Bundle", "cutoff_at": "2026-09-01T00:00:00Z"},
                )
            )
        )
        inbox = tmp_path / "inbox"
        inbox.mkdir(exist_ok=True)
        (inbox / "evidence.md").write_text("A controlled source record.", encoding="utf-8")
        connected = value(
            await runtime.bus.dispatch(
                request(
                    "project/source/connect",
                    "source",
                    {
                        "project_id": project,
                        "relative_path": "evidence.md",
                        "media_type": "text/markdown",
                    },
                )
            )
        )
        value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "thread",
                    {"project_id": project, "thread_id": thread, "problem": "Review the source"},
                )
            )
        )
        bundle = factory.bundles[0]
        for participant in vars(bundle).values():
            engine = getattr(participant, "_engine", bundle.ledger.engine)
            assert engine is bundle.ledger.engine
    finally:
        runtime.close()
    assert closed == [factory.bundles[0].ledger]
    reopened = create_runtime(
        tmp_path, storage_factory=factory, resource_scope_policy=fixture_scope_policy()
    )
    try:
        current = value(
            await reopened.bus.query(
                request("thread/read", "read", {"project_id": project, "thread_id": thread})
            )
        )
        assert current["thread_id"] == thread
        sources = value(
            await reopened.bus.query(
                request("project/source/list", "sources", {"project_id": project})
            )
        )
        assert connected["binding"]["binding_id"] in str(sources)
    finally:
        reopened.close()
    assert closed == [b.ledger for b in factory.bundles]
