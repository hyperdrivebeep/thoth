"""The normal acquisition path reaches connector awaits outside the storage transaction."""

from contextvars import copy_context
from pathlib import Path

from pytest import MonkeyPatch
from tests.integration.test_a02_autonomous_acquisition import (
    A02Connector,
    prepare_thread,
    request,
    value,
)

from thoth.domain.connectors import (
    ConnectorAccessRequest,
    ConnectorArtifactRef,
    ConnectorCheckpoint,
    ConnectorFetchResult,
)


async def test_connector_discover_and_fetch_have_no_ambient_storage_transaction(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []
    original_discover, original_fetch = A02Connector.discover, A02Connector.fetch

    def outside_transaction() -> None:
        assert not any(
            item is not None
            for key, item in copy_context().items()
            if key.name == "thoth_sqlite_ambient_transaction"
        )

    async def discover(
        self: A02Connector,
        command: ConnectorAccessRequest,
        checkpoint: ConnectorCheckpoint | None = None,
    ) -> tuple[ConnectorArtifactRef, ...]:
        outside_transaction()
        calls.append(("discover", str(command.selector["relative_path"])))
        result = await original_discover(self, command, checkpoint)
        outside_transaction()
        return result

    async def fetch(
        self: A02Connector,
        command: ConnectorAccessRequest,
        ref: ConnectorArtifactRef,
        checkpoint: ConnectorCheckpoint | None = None,
    ) -> ConnectorFetchResult:
        outside_transaction()
        calls.append(("fetch", str(command.selector["relative_path"])))
        result = await original_fetch(self, command, ref, checkpoint)
        outside_transaction()
        return result

    monkeypatch.setattr(A02Connector, "discover", discover)
    monkeypatch.setattr(A02Connector, "fetch", fetch)
    runtime, _, project_id = await prepare_thread(tmp_path, allow_connector=True)
    try:
        analyzed = value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "acquisition-io-boundary",
                    {"project_id": project_id, "thread_id": f"thread:{project_id}"},
                )
            )
        )
        assert "autonomous_acquisition" in analyzed
        assert calls == [
            ("discover", "initial.md"),
            ("fetch", "initial.md"),
            ("discover", "catalog.md"),
            ("fetch", "catalog.md"),
        ]
    finally:
        runtime.close()
