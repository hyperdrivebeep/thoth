"""The normal application entry must use the selected whole-store factory."""

from pathlib import Path

from tests.integration.storage_coverage_helpers import request, value

from thoth.apps.runtime import create_runtime
from thoth.ports.store_bundle import StoreBundlePort


class RecordingStoreFactory:
    provider_id = "recording-sqlite"
    version = "1.0.0"

    def __init__(self) -> None:
        self.opened: list[Path] = []

    def open(self, workspace: Path) -> StoreBundlePort:
        from thoth.adapters.storage.bundle import SqliteStoreFactory

        self.opened.append(workspace)
        return SqliteStoreFactory().open(workspace)


async def test_normal_project_entry_uses_selected_store_factory_and_reopens(tmp_path: Path) -> None:
    factory = RecordingStoreFactory()
    runtime = create_runtime(tmp_path, storage_factory=factory)
    try:
        created = value(
            await runtime.bus.dispatch(
                request(
                    "project/create",
                    "factory-create",
                    {
                        "project_id": "project:store-factory",
                        "name": "Store factory integration",
                        "cutoff_at": "2026-09-08T00:00:00Z",
                    },
                )
            )
        )
        assert created["project_id"] == "project:store-factory"
        assert factory.opened == [tmp_path]
    finally:
        runtime.close()
    reopened = create_runtime(tmp_path, storage_factory=factory)
    try:
        current = value(
            await reopened.bus.dispatch(
                request("project/read", "factory-reopen", {"project_id": "project:store-factory"})
            )
        )
        assert current["name"] == "Store factory integration"
        assert factory.opened == [tmp_path, tmp_path]
    finally:
        reopened.close()
