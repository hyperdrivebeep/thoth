"""Normal dispatch/query entry, synthetic assets only; no remote model or weights."""

from pathlib import Path

import pytest
from tests.embedding_fixtures import sample_assets
from tests.integration.storage_coverage_helpers import request, value

from thoth.adapters.models.catalog import StaticModelCatalog
from thoth.adapters.storage.operations import SqliteOperationStore
from thoth.apps.runtime import create_runtime
from thoth.domain.deployment_mode import DeploymentMode
from thoth.protocol.jsonrpc import RpcErrorCode


async def test_default_off_read_configure_cas_and_no_worker(tmp_path: Path) -> None:
    runtime = create_runtime(tmp_path / "workspace", model_catalog=StaticModelCatalog())
    try:
        service = runtime.local_retrieval
        assert service is not None and service.index is None
        project = "project:local-retrieval"
        value(
            await runtime.bus.dispatch(
                request(
                    "project/create",
                    "create",
                    {
                        "project_id": project,
                        "name": "Synthetic",
                        "cutoff_at": "2026-10-01T00:00:00Z",
                    },
                )
            )
        )
        before = value(
            await runtime.bus.query(
                request("project/retrieval/experiment/read", "read", {"project_id": project})
            )
        )
        assert before["state"] == "OFF" and before["generation"] == 0
        asset = value(
            await runtime.bus.query(
                request("model/localAsset/read", "asset", {"project_id": "system:workspace"})
            )
        )
        assert asset["asset_state"] == "MODEL_REQUIRED" and asset["worker_loaded"] is False
        assert not (tmp_path / "workspace/models").exists()
        command = request(
            "project/retrieval/experiment/configure",
            "enable",
            {
                "project_id": project,
                "enabled": True,
                "expected_generation": 0,
            },
        )
        enabled = value(await runtime.bus.dispatch(command))
        assert enabled["state"] == "NO_SOURCES" and enabled["generation"] == 1
        assert value(await runtime.bus.dispatch(command)) == enabled
        stale = await runtime.bus.dispatch(
            request(
                "project/retrieval/experiment/configure",
                "stale",
                {
                    "project_id": project,
                    "enabled": False,
                    "expected_generation": 0,
                },
            )
        )
        assert stale.error and stale.error.data["reason_code"] == "EMBEDDING_CONFIG_CONFLICT"
        assert service.index is None
    finally:
        runtime.close()


async def test_manual_import_dispatch_replay_and_manifest_mismatch(tmp_path: Path) -> None:
    runtime = create_runtime(tmp_path / "workspace", model_catalog=StaticModelCatalog())
    source, assets = sample_assets(tmp_path)
    try:
        assert runtime.local_retrieval is not None
        runtime.local_retrieval.assets = assets
        body: dict[str, object] = {
            "project_id": "system:workspace",
            "source_path": str(source),
            "manifest_digest": assets.manifest.digest,
        }
        command = request("model/localAsset/import", "import", body)
        result = value(await runtime.bus.dispatch(command))
        assert result["state"] == "READY"
        assert value(await runtime.bus.dispatch(command)) == result
        mismatch = await runtime.bus.dispatch(
            request(
                "model/localAsset/import", "bad-manifest", {**body, "manifest_digest": "f" * 64}
            )
        )
        assert mismatch.error and mismatch.error.data["reason_code"] == "MODEL_MANIFEST_CHANGED"
        assert assets.status().state == "READY"
        assert runtime.local_retrieval.index is None
    finally:
        runtime.close()


@pytest.mark.parametrize("consent", [False, 1, "true", None])
async def test_install_requires_explicit_true_before_claim(
    tmp_path: Path, consent: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = create_runtime(tmp_path, model_catalog=StaticModelCatalog())
    try:
        service = runtime.local_retrieval
        assert service is not None
        operations = SqliteOperationStore(runtime.ledger.engine)
        before = operations.list_by_project("system:workspace")
        action_calls: list[dict[str, object]] = []

        async def forbidden_action(**kwargs: object) -> None:
            action_calls.append(kwargs)
            raise AssertionError("ASSET_ACTION_WITHOUT_CONSENT")

        monkeypatch.setattr(service, "asset_action", forbidden_action)
        response = await runtime.bus.dispatch(
            request(
                "model/localAsset/install",
                "no-consent",
                {
                    "project_id": "system:workspace",
                    "manifest_digest": service.assets.manifest.digest,
                    "confirm_download": consent,
                },
            )
        )
        assert operations.list_by_project("system:workspace") == before, "CLAIM_WITHOUT_CONSENT"
        assert action_calls == []
        assert response.error is not None
        assert response.error.code == RpcErrorCode.INVALID_PARAMS
        assert response.error.message == "MODEL_ASSET_CONSENT_REQUIRED"
        assert not (tmp_path / "models").exists()
    finally:
        runtime.close()


async def test_hosted_denies_before_asset_io(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = create_runtime(
        tmp_path, deployment_mode=DeploymentMode.HOSTED_REVIEW, model_catalog=StaticModelCatalog()
    )
    try:
        assert runtime.local_retrieval is not None

        def forbidden():
            pytest.fail("hosted performed local asset IO")

        monkeypatch.setattr(runtime.local_retrieval.assets, "status", forbidden)
        response = await runtime.bus.query(
            request("model/localAsset/read", "hosted", {"project_id": "system:workspace"})
        )
        assert response.error and response.error.code == -32040
        assert not (tmp_path / "models").exists()
    finally:
        runtime.close()


@pytest.mark.parametrize("method", ["model/localAsset/import", "model/localAsset/install"])
async def test_asset_timeout_is_a_public_reason_not_internal_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, method: str
):
    runtime = create_runtime(tmp_path / "workspace", model_catalog=StaticModelCatalog())
    try:
        assert runtime.local_retrieval is not None

        async def timeout(**_kwargs: object):
            raise TimeoutError

        monkeypatch.setattr(runtime.local_retrieval, "asset_action", timeout)
        body: dict[str, object] = {"project_id": "system:workspace", "manifest_digest": "a" * 64}
        body.update(
            {"source_path": str(tmp_path)}
            if method.endswith("/import")
            else {"confirm_download": True}
        )
        response = await runtime.bus.dispatch(request(method, "timeout", body))
        assert response.error and response.error.code == -32030
        assert response.error.data["reason_code"] == "MODEL_ASSET_TIMEOUT"
    finally:
        runtime.close()
