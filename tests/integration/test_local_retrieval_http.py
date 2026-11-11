import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from tests.embedding_fixtures import FakeWorker, sample_assets
from tests.integration.resource_scope_helpers import scope_harness, value
from tests.integration.storage_coverage_helpers import request

from thoth.adapters.http.app import create_app
from thoth.adapters.models.catalog import StaticModelCatalog
from thoth.apps.runtime import create_runtime


async def test_http_async_import_poll_replay_and_cancel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source, assets = sample_assets(tmp_path)
    runtime = create_runtime(tmp_path / "workspace", model_catalog=StaticModelCatalog())
    assert runtime.local_retrieval is not None
    runtime.local_retrieval.assets = assets
    app = create_app(runtime.bus)
    try:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8765"
            ) as client,
        ):
            command = request(
                "model/localAsset/import",
                "synthetic-import",
                {
                    "project_id": "system:workspace",
                    "source_path": str(source),
                    "manifest_digest": assets.manifest.digest,
                },
            ).model_dump(mode="json", by_alias=True)
            start = (await client.post("/rpc/async", json=command)).json()
            operation = start["result"]["operation_id"]
            async with asyncio.timeout(5):
                while True:
                    result = (
                        await client.get(f"/operations/{operation}?project_id=system:workspace")
                    ).json()
                    if result["state"] != "RUNNING":
                        break
                    await asyncio.sleep(0.01)
            assert result["state"] == "SUCCEEDED" and result["result"]["state"] == "READY"
            replay = (await client.post("/rpc/async", json=command)).json()
            assert replay["result"]["operation_id"] == operation
            entered, stopped = asyncio.Event(), asyncio.Event()

            async def waiting(*args: Any, **kwargs: Any):
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    stopped.set()

            monkeypatch.setattr(assets, "import_directory", waiting)
            command["params"]["_meta"]["idempotencyKey"] = "synthetic-cancel"
            second = (await client.post("/rpc/async", json=command)).json()["result"][
                "operation_id"
            ]
            await asyncio.wait_for(entered.wait(), 3)
            cancel = await client.post(
                f"/operations/{second}/cancel", json={"project_id": "system:workspace"}
            )
            assert cancel.status_code == 200
            await asyncio.wait_for(stopped.wait(), 3)
            async with asyncio.timeout(3):
                while True:
                    result = (
                        await client.get(f"/operations/{second}?project_id=system:workspace")
                    ).json()
                    if result["state"] == "CANCELLED":
                        break
                    await asyncio.sleep(0.01)
            assert assets.status().state == "READY"
    finally:
        runtime.close()


async def test_project_authentication_cannot_mutate_another_scope(tmp_path: Path):
    async with scope_harness(tmp_path) as harness:
        denied = await harness.call(
            "alpha",
            "project/retrieval/experiment/configure",
            "workstream-denied",
            {"enabled": False, "expected_generation": 1},
        )
        assert denied.json()["error"]["data"]["reason_code"] == "LOCAL_RETRIEVAL_SCOPE_DENIED"
        wrong = await harness.call(
            "owner",
            "project/retrieval/experiment/configure",
            "wrong-project",
            {"project_id": "project:other", "enabled": False, "expected_generation": 1},
        )
        assert "error" in wrong.json()
        service = harness.runtime.local_retrieval
        assert service is not None
        # Neither call changed anything: the project still reads as the untouched default (on).
        config = service.store.configuration(harness.project)
        assert (config.enabled, config.generation) == (True, 1)


async def test_role_revoked_during_encoding_prevents_ready_publication(tmp_path: Path):
    source, assets = sample_assets(tmp_path)
    await assets.import_directory(source)
    async with scope_harness(tmp_path / "workspace") as h:
        service = h.runtime.local_retrieval
        assert service is not None
        service.assets = assets
        service.dependency_available = lambda: True
        service.index = service.factory(assets.descriptor())
        worker = FakeWorker()
        worker.release = asyncio.Event()
        service.index.worker = worker
        await h.connect(
            "owner",
            "synthetic-index-source",
            {
                "owner_kind": "PROJECT",
                "visibility": "PROJECT_SHARED",
            },
            confirm_synthetic_time=True,
        )
        configured = value(
            await h.call(
                "owner",
                "project/retrieval/experiment/configure",
                "enable",
                {
                    "enabled": True,
                    "expected_generation": 1,
                },
            )
        )
        task = asyncio.create_task(
            h.call(
                "owner",
                "project/retrieval/experiment/prepare",
                "prepare",
                {
                    "expected_generation": configured["generation"],
                },
            )
        )
        try:
            await asyncio.wait_for(worker.entered.wait(), 5)
            role = next(
                r
                for r in service.governance.list_roles(h.project)
                if r.actor_id == h.actors["owner"]
            )
            service.governance.revoke_role(
                h.project, role.role_assignment_id, revoked_at=datetime.now(UTC).isoformat()
            )
            worker.release.set()
            response = await asyncio.wait_for(task, 5)
            assert response.json()["error"]["data"]["reason_code"] == "RESOURCE_AUTHORITY_CHANGED"
            assert all(job.state != "READY" for job in service.store.jobs(h.project))
        finally:
            worker.release.set()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
