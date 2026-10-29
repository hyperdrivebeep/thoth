from pathlib import Path

import pytest
from tests.embedding_fixtures import FakeWorker
from tests.integration.scoped_runtime import create_runtime
from tests.integration.test_connector_sandbox_cycle import request, value

from thoth.protocol.bus import CommandBus


@pytest.mark.parametrize("enabled", [False, True])
async def test_normal_source_connect_commit_then_explicit_worker(tmp_path: Path, enabled: bool):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "input.txt").write_text("Synthetic source one.\n\nSynthetic source two.")
    worker = FakeWorker()
    runtime = create_runtime(tmp_path, embedding_worker=worker)
    service = runtime.embedding_index
    assert service is not None
    project = "project:embedding-rpc"
    try:
        value(
            await runtime.bus.dispatch(
                request(
                    "project/create",
                    "project",
                    {
                        "project_id": project,
                        "name": "Synthetic",
                        "cutoff_at": "2026-10-01T00:00:00Z",
                    },
                )
            )
        )
        if enabled:
            service.configure(project, True, 0)
        value(
            await runtime.bus.dispatch(
                request(
                    "project/source/connect",
                    "source",
                    {
                        "project_id": project,
                        "relative_path": "input.txt",
                        "media_type": "text/plain",
                        "authority": "OFFICIAL",
                        "cutoff_state": "ELIGIBLE",
                        "security_class": "INTERNAL",
                    },
                )
            )
        )
        assert worker.calls == []
        jobs = service.store.jobs(project)
        if enabled:
            assert len(jobs) == 1 and jobs[0].state == "PENDING"
            (ready,) = await service.drain(project)
            assert ready.state == "READY"
            spans = service.artifacts.list_evidence(project)
            assert len(service.read_vectors(project, spans)) == len(spans) > 0
        else:
            assert jobs == ()
    finally:
        runtime.close()
    assert worker.closed


def test_default_runtime_has_no_embedding_worker(tmp_path: Path):
    runtime = create_runtime(tmp_path)
    try:
        assert runtime.embedding_index is None
    finally:
        runtime.close()


@pytest.mark.parametrize("point", ["after_ingestion", "after_source_projection", "after_receipts"])
async def test_acquisition_fault_rolls_back_source_and_intent(tmp_path: Path, point: str):
    (tmp_path / "inbox").mkdir()
    (tmp_path / "inbox" / "input.txt").write_text("Synthetic rollback source.")
    worker = FakeWorker()

    def fault(actual: str) -> None:
        if actual == point:
            raise RuntimeError("synthetic acquisition fault")

    runtime = create_runtime(tmp_path, embedding_worker=worker, acquisition_fault_injector=fault)
    service = runtime.embedding_index
    assert service is not None
    try:
        value(
            await runtime.bus.dispatch(
                request(
                    "project/create",
                    "project",
                    {
                        "project_id": "project:rollback",
                        "name": "Synthetic",
                        "cutoff_at": "2026-10-01T00:00:00Z",
                    },
                )
            )
        )
        service.configure("project:rollback", True, 0)
        result = await runtime.bus.dispatch(
            request(
                "project/source/connect",
                "source",
                {
                    "project_id": "project:rollback",
                    "relative_path": "input.txt",
                    "media_type": "text/plain",
                    "authority": "OFFICIAL",
                    "cutoff_state": "ELIGIBLE",
                    "security_class": "INTERNAL",
                },
            )
        )
        assert result.error is not None
        assert service.store.jobs("project:rollback") == ()
        assert service.artifacts.list_artifacts("project:rollback") == ()
        assert worker.calls == []
    finally:
        runtime.close()


def test_failed_runtime_startup_closes_optional_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    worker = FakeWorker()

    def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("synthetic startup")

    monkeypatch.setattr(CommandBus, "__init__", fail)
    with pytest.raises(RuntimeError, match="synthetic startup"):
        create_runtime(tmp_path, embedding_worker=worker)
    assert worker.closed


def test_worker_cleanup_failure_preserves_startup_error_and_releases_storage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingCloseWorker(FakeWorker):
        def close(self) -> None:
            super().close()
            raise RuntimeError("synthetic cleanup")

    worker = FailingCloseWorker()
    original = ValueError("original startup")

    def fail(*args: object, **kwargs: object) -> None:
        raise original

    with monkeypatch.context() as patch:
        patch.setattr(CommandBus, "__init__", fail)
        with pytest.raises(ValueError) as raised:
            create_runtime(tmp_path, embedding_worker=worker)
    assert raised.value is original and worker.closed
    assert any("embedding" in note for note in original.__notes__)
    runtime = create_runtime(tmp_path)
    runtime.close()
