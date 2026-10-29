"""Runtime ownership includes an active real child running only a synthetic encoder."""

import asyncio
from pathlib import Path

import pytest
from tests.embedding_fixtures import process_is_gone
from tests.integration.scoped_runtime import create_runtime
from tests.integration.test_connector_sandbox_cycle import request, value

from thoth.adapters.embeddings.process_worker import ProcessEmbeddingWorker
from thoth.adapters.storage.bundle import SqliteStoreFactory
from thoth.domain.embedding_index import EmbeddingIndexError


async def test_runtime_close_fences_job_and_stops_active_owned_child(tmp_path: Path) -> None:
    (tmp_path / "inbox").mkdir()
    (tmp_path / "inbox" / "input.txt").write_text("sleep:30")
    worker = ProcessEmbeddingWorker("tests.embedding_fixtures:ChildEncoder")
    runtime = create_runtime(tmp_path, embedding_worker=worker)
    service = runtime.embedding_index
    assert service is not None
    project = "project:process-close"
    task: asyncio.Task[object] | None = None
    closed = False
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
        task = asyncio.create_task(service.drain(project))
        async with asyncio.timeout(3):
            while worker.last_pid is None:
                await asyncio.sleep(0.01)
        pid = worker.last_pid
        runtime.close()
        closed = True
        with pytest.raises((asyncio.CancelledError, EmbeddingIndexError)):
            await task
        assert pid is not None and worker.last_stop_confirmed and process_is_gone(pid)
        reopened = SqliteStoreFactory().open(tmp_path)
        try:
            (job,) = reopened.embedding_index.jobs(project)
            assert job.state == "CANCELLED" and job.owner is None
        finally:
            reopened.close()
    finally:
        if task is not None and not task.done():
            task.cancel()
        await worker.aclose()
        if not closed:
            runtime.close()
