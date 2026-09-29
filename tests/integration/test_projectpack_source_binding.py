"""Pack intake persists source authority; revocation still blocks restore."""

from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue
from tests.integration.test_four_projectpack_portability import GenericProjectPackModel

from thoth.adapters.projectpacks import load_project_pack
from thoth.adapters.storage.evidence_graph import SqliteEvidenceGraphStore
from thoth.adapters.storage.governance import SqliteGovernanceStore
from thoth.apps.projectpack_execution import run_project_pack
from thoth.apps.runtime import create_runtime
from thoth.domain.governance import SourceBinding
from thoth.protocol.jsonrpc import JsonRpcRequest, JsonRpcResponse


def request(method: str, key: str, payload: dict[str, object]) -> JsonRpcRequest:
    return JsonRpcRequest.model_validate(
        {
            "id": key,
            "method": method,
            "params": {"_meta": {"idempotencyKey": key}, "input": payload},
        }
    )


def value(response: JsonRpcResponse) -> dict[str, JsonValue]:
    assert response.error is None
    assert response.result is not None
    return cast(dict[str, JsonValue], response.result["value"])


@pytest.mark.parametrize("mode", ("DETACH", "REVOKE"))
async def test_pack_source_deactivation_blocks_restore_without_erasing_provenance(
    tmp_path: Path, mode: str
) -> None:
    root = Path(__file__).resolve().parents[2]
    pack = load_project_pack(root / "examples/projectpacks/6g-sandbox-hero")
    workspace = tmp_path / "pack"
    initial = await run_project_pack(pack, workspace=workspace, model=GenericProjectPackModel())
    runtime = create_runtime(workspace)
    project_id = pack.project.project_id
    try:
        listed = value(
            await runtime.bus.dispatch(
                request("project/source/list", "pack-sources", {"project_id": project_id})
            )
        )
        bindings = cast(list[dict[str, JsonValue]], listed["bindings"])
        assert len(bindings) == len(pack.sources) == len(initial.project.source_binding_ids)
        evidence = value(
            await runtime.bus.dispatch(
                request("evidence/list", "pack-evidence", {"project_id": project_id})
            )
        )
        eligible = next(
            item
            for item in cast(list[dict[str, JsonValue]], evidence["spans"])
            if item["cutoff_state"] == "ELIGIBLE"
        )
        binding = next(item for item in bindings if item["artifact_id"] == eligible["artifact_id"])
        object_record = cast(
            dict[str, JsonValue],
            value(
                await runtime.bus.dispatch(
                    request(
                        "object/read",
                        "pack-object",
                        {"project_id": project_id, "object_id": pack.scenario.object_id},
                    )
                )
            )["object"],
        )
        original_head = str(object_record["revision_digest"])
        artifact_id = str(binding["artifact_id"])
        source_before = SqliteEvidenceGraphStore(runtime.ledger.engine).read_source_by_artifact(
            artifact_id
        )
        assert source_before is not None
        cutoff_basis = cast(dict[str, JsonValue], listed["cutoff_basis"])
        deactivated = value(
            await runtime.bus.dispatch(
                request(
                    "project/source/disconnect",
                    "pack-deactivate",
                    {
                        "project_id": project_id,
                        "binding_id": binding["binding_id"],
                        "mode": mode,
                        "expected_project_revision": cutoff_basis["project_revision"],
                    },
                )
            )
        )
        assert cast(dict[str, JsonValue], deactivated["binding"])["state"] == (
            "REVOKED" if mode == "REVOKE" else "DETACHED"
        )
        heads_before = dict(runtime.ledger.read_heads(project_id))
        denied = await runtime.bus.dispatch(
            request(
                "revision/restore/propose",
                "pack-restore-denied",
                {
                    "project_id": project_id,
                    "aggregate_id": pack.scenario.object_id,
                    "current_head_digest": original_head,
                    "target_revision_digest": original_head,
                    "reason": "deactivated pack input cannot authorize restore",
                    "evidence_refs": [],
                    "actor_or_agent_ref": "human:pack-reviewer",
                },
            )
        )
        assert denied.error is not None
        assert denied.error.data["reason_code"] in {
            "RESTORE_SOURCE_UNAVAILABLE",
            "RESTORE_ACCESS_DENIED",
        }
        assert runtime.ledger.read_heads(project_id) == heads_before
        source_after = SqliteEvidenceGraphStore(runtime.ledger.engine).read_source_by_artifact(
            artifact_id
        )
        assert source_after is not None
        assert source_after.sha256 == source_before.sha256
        assert source_after.source_id == source_before.source_id
    finally:
        runtime.close()


async def test_pack_binding_failure_preserves_prior_source_without_orphaning_failed_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = Path(__file__).resolve().parents[2]
    pack = load_project_pack(root / "examples/projectpacks/6g-sandbox-hero")
    workspace = tmp_path / "partial-pack"
    original = SqliteGovernanceStore.put_source_binding
    calls = 0

    def fail_second(self: SqliteGovernanceStore, binding: SourceBinding) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("synthetic second binding failure")
        original(self, binding)

    with monkeypatch.context() as patch:
        patch.setattr(SqliteGovernanceStore, "put_source_binding", fail_second)
        with pytest.raises(RuntimeError, match="synthetic second binding failure"):
            await run_project_pack(pack, workspace=workspace, model=GenericProjectPackModel())
    runtime = create_runtime(workspace)
    try:
        listed = value(
            await runtime.bus.dispatch(
                request(
                    "project/source/list",
                    "partial-pack-sources",
                    {"project_id": pack.project.project_id},
                )
            )
        )
        artifacts = cast(list[dict[str, JsonValue]], listed["artifacts"])
        bindings = cast(list[dict[str, JsonValue]], listed["bindings"])
        assert calls == 2
        assert len(artifacts) == len(bindings) == 1
        assert (
            artifacts[0]["source_uri"]
            == f"projectpack://{pack.project.pack_id}/{pack.sources[0].path}"
        )
        assert bindings[0]["artifact_id"] == artifacts[0]["artifact_id"]
        assert bindings[0]["state"] == "ACTIVE"
    finally:
        runtime.close()
