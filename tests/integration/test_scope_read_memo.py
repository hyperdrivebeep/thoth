from contextvars import ContextVar
from pathlib import Path
from types import MethodType
from typing import cast

import httpx
import pytest
from pydantic import JsonValue
from tests.atomicity.harness import assert_phase_delta, snapshot
from tests.integration.storage_coverage_helpers import request
from tests.integration.test_historical_access_verification import prepared, sealed_failure
from tests.integration.test_research_focus_basis import research_host
from tests.integration.test_research_request_v2 import ControlledResearchModel, setup

from thoth.adapters.storage.resource_scope import SqliteResourceScopeStore
from thoth.application.commands.operations import OperationCommandHandlers
from thoth.application.services import resource_scope_read_context
from thoth.application.services.historical_access_verification import (
    require_historical_operation_access,
)
from thoth.application.services.resource_scope_read_context import (
    ReadLookupMemo,
    ScopeReadContext,
    scope_read_transaction,
)
from thoth.application.services.resource_scope_service import ResourceScopeService
from thoth.apps.runtime_types import AppRuntime
from thoth.domain.resource_scope import (
    ResourceScopeBody,
    ResourceScopeReceipt,
    ResourceScopeRecord,
    current_resource_uses,
    seal_resource_scope,
)
from thoth.protocol.jsonrpc import JsonRpcResponse
from thoth.protocol.registry import MethodRegistry


def _record(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    assert all(isinstance(key, str) for key in cast(dict[object, object], value))
    return cast(dict[str, object], value)


def _text(value: object) -> str:
    assert isinstance(value, str)
    return value


def _integer(value: object) -> int:
    assert type(value) is int
    return value


def _rpc_value(response: JsonRpcResponse) -> dict[str, object]:
    assert response.error is None, response.error
    assert response.result is not None
    return _record(response.result["value"])


def _http_value(response: httpx.Response) -> dict[str, object]:
    assert response.status_code == 200, response.text
    body = _record(response.json())
    assert "error" not in body, body
    return _record(_record(body["result"])["value"])


def _registry(runtime: AppRuntime) -> MethodRegistry:
    registry: object = vars(runtime.bus).get("_registry")
    assert isinstance(registry, MethodRegistry)
    return registry


def _operation_scopes(runtime: AppRuntime) -> ResourceScopeService:
    handler = _registry(runtime).resolve("operation/result/read")
    assert isinstance(handler, MethodType)
    owner = handler.__self__
    assert isinstance(owner, OperationCommandHandlers)
    scopes: object = vars(owner).get("_resource_access")
    assert isinstance(scopes, ResourceScopeService)
    return scopes


def _lookup_memo() -> ReadLookupMemo | None:
    variable: object = vars(resource_scope_read_context).get("_READ_LOOKUPS")
    assert isinstance(variable, ContextVar)
    memo: object = cast(ContextVar[ReadLookupMemo | None], variable).get()
    assert memo is None or isinstance(memo, ReadLookupMemo)
    return memo


def _scope_store(scopes: ResourceScopeService) -> SqliteResourceScopeStore:
    store: object = vars(scopes).get("_store")
    assert isinstance(store, SqliteResourceScopeStore)
    return store


def _scope_receipt(record: ResourceScopeRecord) -> ResourceScopeReceipt:
    builder: object = vars(ResourceScopeService).get("_receipt")
    assert callable(builder)
    receipt: object = builder(record)
    assert isinstance(receipt, ResourceScopeReceipt)
    return receipt


async def test_historical_memo_hits_still_record_actual_uses_and_deny_4097(
    tmp_path: Path,
) -> None:
    runtime, host, refs = await prepared(tmp_path)
    try:
        sealed = sealed_failure(host, refs[:4096], "operation:memo-historical")

        async def read(inputs: dict[str, JsonValue]) -> dict[str, JsonValue]:
            count = _integer(inputs["count"])
            with host.records.ledger.transaction(), scope_read_transaction():
                require_historical_operation_access(host.access, sealed)
                assert current_resource_uses() == ()
                host.access.require_reads("p", refs[:count])
                return {"uses": len(current_resource_uses() or ())}

        _registry(runtime).decorate("operation/result/read", lambda _: read)
        before = snapshot(runtime.ledger.engine)
        allowed = _rpc_value(
            await runtime.bus.query(
                request("operation/result/read", "allow", {"project_id": "p", "count": 4096})
            )
        )
        assert _integer(allowed["uses"]) == 4096
        denied = await runtime.bus.query(
            request("operation/result/read", "limit", {"project_id": "p", "count": 4097})
        )
        assert denied.error is not None and "RESOURCE_SCOPE_USAGE_LIMIT" in str(denied.error)
        again = _rpc_value(
            await runtime.bus.query(
                request("operation/result/read", "fresh", {"project_id": "p", "count": 1})
            )
        )
        assert _integer(again["uses"]) == 1
        assert_phase_delta(before, snapshot(runtime.ledger.engine))
    finally:
        runtime.close()


async def test_shallow_then_deep_root_keeps_depth_guard_and_parent_changes_refresh(
    tmp_path: Path,
) -> None:
    runtime = await setup(tmp_path, ControlledResearchModel())
    try:
        host = research_host(runtime)
        scopes = host.access
        assert isinstance(scopes, ResourceScopeService)
        store = _scope_store(scopes)
        artifact = host.analysis.evidence("p")[0].artifact_id
        seed = scopes.ensure_derived("p", "depth:0", (artifact,), is_new=True)
        for index in range(1, 130):
            body = ResourceScopeBody.model_validate(seed.model_dump(exclude={"record_digest"}))
            record = seal_resource_scope(
                body.model_copy(
                    update={
                        "scope_id": f"scope:depth:{index}",
                        "resource_ref": f"depth:{index}",
                        "parent_refs": (f"depth:{index - 1}",),
                        "receipt_ref": f"receipt:depth:{index}",
                    }
                )
            )
            store.append(record, _scope_receipt(record), expected_revision=0)

        async def read(inputs: dict[str, JsonValue]) -> dict[str, JsonValue]:
            root = _text(inputs["root"])
            with host.records.ledger.transaction(), scope_read_transaction():
                host.access.require_read("p", "depth:60")
                host.access.require_read("p", root)
                return {"ok": True}

        _registry(runtime).decorate("operation/result/read", lambda _: read)
        before = snapshot(runtime.ledger.engine)
        assert _rpc_value(
            await runtime.bus.query(
                request("operation/result/read", "shallow", {"project_id": "p", "root": "depth:61"})
            )
        )["ok"]
        deep = await runtime.bus.query(
            request("operation/result/read", "deep", {"project_id": "p", "root": "depth:129"})
        )
        assert deep.error is not None and "RESOURCE_SCOPE_LINEAGE_INVALID" in str(deep.error)
        assert_phase_delta(before, snapshot(runtime.ledger.engine))
        old = store.read("p", "depth:61")
        assert old is not None
        body = ResourceScopeBody.model_validate(old.model_dump(exclude={"record_digest"}))
        changed = seal_resource_scope(
            body.model_copy(
                update={
                    "revision": 2,
                    "previous_digest": old.record_digest,
                    "parent_refs": ("missing-parent",),
                    "receipt_ref": "receipt:changed-parent",
                }
            )
        )
        store.append(changed, _scope_receipt(changed), expected_revision=1)
        denied = await runtime.bus.query(
            request("operation/result/read", "changed", {"project_id": "p", "root": "depth:61"})
        )
        assert denied.error is not None
    finally:
        runtime.close()


async def test_real_grant_revocation_is_not_reused_by_next_memo_query(
    tmp_path: Path,
) -> None:
    from tests.integration.resource_scope_helpers import scope_harness, value
    from tests.integration.test_resource_scope_revocation import historical_query

    async with scope_harness(tmp_path) as h:
        connected = value(
            await h.connect(
                "alpha",
                "memo-private",
                {
                    "owner_kind": "WORKSTREAM",
                    "owner_workstream": "alpha",
                    "visibility": "WORKSTREAM",
                },
            )
        )
        artifact = connected["artifact"]["artifact_id"]
        scope = value(
            await h.call("alpha", "project/source/scope/read", "scope", {"resource_ref": artifact})
        )["scope"]
        grant = value(
            await h.call(
                "alpha",
                "project/source/scope/grant",
                "grant",
                {
                    "resource_ref": artifact,
                    "expected_revision": scope["revision"],
                    "grantee_kind": "ACTOR",
                    "grantee_ref": h.actors["beta"],
                    "reason": "controlled query grant",
                },
            )
        )["scope"]
        host = research_host(h.runtime)

        async def read(inputs: dict[str, JsonValue]) -> dict[str, JsonValue]:
            with host.records.ledger.transaction(), scope_read_transaction():
                host.access.require_read(h.project, artifact)
                host.access.require_read(h.project, artifact)
                return {"authorized": True}

        _registry(h.runtime).decorate("operation/result/read", lambda _: read)
        assert _http_value(
            await historical_query(h, "operation/result/read", "before", "operation:unused")
        )["authorized"]
        value(
            await h.call(
                "alpha",
                "project/source/scope/revoke",
                "revoke",
                {
                    "resource_ref": artifact,
                    "expected_revision": grant["revision"],
                    "grant_id": grant["grants"][0]["grant_id"],
                    "reason": "controlled revoke",
                },
            )
        )
        response = await historical_query(h, "operation/result/read", "after", "operation:unused")
        body = _record(response.json())
        assert "RESOURCE_ACCESS_DENIED" in str(body) and "authorized" not in str(body)


async def test_authenticated_query_preclaim_uses_read_memo_but_dispatch_does_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.integration.resource_scope_helpers import scope_harness, value
    from tests.integration.test_resource_scope_revocation import historical_query

    async with scope_harness(tmp_path) as h:
        connected = value(
            await h.connect(
                "alpha",
                "memo-preclaim-private",
                {
                    "owner_kind": "WORKSTREAM",
                    "owner_workstream": "alpha",
                    "visibility": "WORKSTREAM",
                },
            )
        )
        artifact = connected["artifact"]["artifact_id"]
        scope = value(
            await h.call(
                "alpha",
                "project/source/scope/read",
                "scope-preclaim",
                {"resource_ref": artifact},
            )
        )["scope"]
        value(
            await h.call(
                "alpha",
                "project/source/scope/grant",
                "grant-preclaim",
                {
                    "resource_ref": artifact,
                    "expected_revision": scope["revision"],
                    "grantee_kind": "ACTOR",
                    "grantee_ref": h.actors["beta"],
                    "reason": "controlled query preclaim grant",
                },
            )
        )
        spans = value(await h.call("beta", "evidence/list", "preclaim-spans", {}))["spans"]
        span = next(item for item in spans if item["artifact_id"] == artifact)
        read = await h.call(
            "beta", "evidence/read", "preclaim-source-read", {"span_id": span["span_id"]}
        )
        operation_id = _text(_record(_record(read.json())["result"])["operation_id"])
        scopes = _operation_scopes(h.runtime)
        observed: list[bool] = []
        original: object = vars(ResourceScopeService).get("_required_record")
        assert callable(original)

        def record_memo_state(
            project_id: str, resource_ref: str, context: ScopeReadContext | None = None
        ) -> ResourceScopeRecord:
            memo = _lookup_memo()
            observed.append(memo is not None and memo.active)
            result: object = original(scopes, project_id, resource_ref, context)
            assert isinstance(result, ResourceScopeRecord)
            return result

        monkeypatch.setattr(scopes, "_required_record", record_memo_state)
        query = await historical_query(h, "operation/result/read", "query-preclaim", operation_id)
        assert "error" not in _record(query.json())
        assert observed and all(observed)
        observed.clear()
        dispatch = await h.call(
            "beta",
            "operation/result/read",
            "dispatch-preclaim",
            {"operation_id": operation_id},
        )
        assert "error" not in _record(dispatch.json())
        assert observed and not any(observed)
