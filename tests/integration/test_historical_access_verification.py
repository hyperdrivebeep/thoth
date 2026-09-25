"""Sealed ACL verification is query-only; new consumption still has the existing cap."""

from pathlib import Path
from typing import cast

import httpx
import pytest
from pydantic import JsonValue
from tests.atomicity.harness import assert_phase_delta, snapshot
from tests.integration.storage_coverage_helpers import request
from tests.integration.test_research_focus_basis import research_host
from tests.integration.test_research_request_v2 import ControlledResearchModel, setup

from thoth.application.commands.research_threads import ResearchThreadHandlers
from thoth.application.services.historical_access_verification import (
    require_historical_operation_access,
)
from thoth.apps.runtime_types import AppRuntime
from thoth.domain.enums import OperationState
from thoth.domain.operation import OperationRecord
from thoth.domain.resource_scope import current_resource_uses, resource_use_scope
from thoth.protocol.jsonrpc import JsonRpcResponse
from thoth.protocol.registry import MethodRegistry


def _record(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    assert all(isinstance(key, str) for key in cast(dict[object, object], value))
    return cast(dict[str, object], value)


def _list(value: object) -> list[object]:
    assert isinstance(value, list)
    return cast(list[object], value)


def _text(value: object) -> str:
    assert isinstance(value, str)
    return value


def _rpc_record(response: JsonRpcResponse) -> dict[str, object]:
    assert response.error is None, response.error
    assert response.result is not None
    return _record(response.result["value"])


def _http_value(response: httpx.Response) -> dict[str, object]:
    assert response.status_code == 200, response.text
    body = _record(response.json())
    assert "error" not in body, body
    return _record(_record(body["result"])["value"])


def _registry(runtime: AppRuntime) -> MethodRegistry:
    registry = vars(runtime.bus).get("_registry")
    assert isinstance(registry, MethodRegistry)
    return registry


async def prepared(tmp_path: Path) -> tuple[AppRuntime, ResearchThreadHandlers, tuple[str, ...]]:
    runtime = await setup(tmp_path, ControlledResearchModel(), source=False)
    (tmp_path / "inbox").mkdir(exist_ok=True)
    (tmp_path / "inbox/refs.md").write_text(
        "\n".join(f"authorized row {n}" for n in range(4100)), encoding="utf-8"
    )
    _rpc_record(
        await runtime.bus.dispatch(
            request(
                "project/source/connect",
                "refs",
                {
                    "project_id": "p",
                    "relative_path": "refs.md",
                    "media_type": "text/markdown",
                    "authority": "INFORMAL",
                    "cutoff_state": "ELIGIBLE",
                    "security_class": "INTERNAL",
                },
            )
        )
    )
    host = research_host(runtime)
    refs = tuple(span.span_id for span in host.analysis.evidence("p"))
    return runtime, host, refs


def sealed_failure(
    host: ResearchThreadHandlers, refs: tuple[str, ...], identifier: str
) -> OperationRecord:
    operation = OperationRecord(
        operation_id=identifier,
        project_id="p",
        method="thread/start",
        idempotency_key=identifier,
        scope_digest="a" * 64,
        state=OperationState.RUNNING,
        created_at=host.records.clock.now(),
    )
    host.operations.claim(operation)
    with resource_use_scope("p"):
        host.access.require_reads("p", refs)
        host.operations.fail(
            identifier,
            {"code": -32603, "message": "controlled failure"},
            completed_at=host.records.clock.now(),
        )
    sealed = host.operations.read(identifier)
    assert sealed is not None
    return sealed


class CompositeQuery:
    def __init__(
        self, host: ResearchThreadHandlers, operation: OperationRecord, new_refs: tuple[str, ...]
    ) -> None:
        self.host, self.operation, self.new_refs = host, operation, new_refs
        self.preclaim_allowed = False

    def authorize_before_claim(self, method: str, payload: dict[str, JsonValue]) -> None:
        require_historical_operation_access(self.host.access, self.operation)
        self.preclaim_allowed = True

    async def read(self, payload: dict[str, JsonValue]) -> dict[str, JsonValue]:
        # Same immutable envelope is verified in preclaim and handler, with all current ACLs.
        require_historical_operation_access(self.host.access, self.operation)
        self.host.access.require_reads("p", self.new_refs)
        new_uses: list[JsonValue] = [
            {
                "project_id": use.project_id,
                "resource_ref": use.resource_ref,
                "capability": use.capability,
            }
            for use in current_resource_uses() or ()
        ]
        return {
            "state": self.operation.state.value,
            "new_uses": new_uses,
        }


async def test_maximum_failure_binding_query_adds_real_read_without_mutating_history(
    tmp_path: Path,
) -> None:
    runtime, host, refs = await prepared(tmp_path)
    try:
        operation = sealed_failure(host, refs[:4096], "operation:wide")
        assert operation.resource_uses is not None
        assert len(operation.resource_uses) == 4096
        query = CompositeQuery(host, operation, refs[4096:4097])
        _registry(runtime).decorate("operation/result/read", lambda _: query.read)
        before = snapshot(runtime.ledger.engine)
        result = _rpc_record(
            await runtime.bus.query(
                request(
                    "operation/result/read",
                    "query",
                    {"project_id": "p", "operation_id": operation.operation_id},
                )
            )
        )
        assert query.preclaim_allowed and result["state"] == "FAILED"
        assert [
            _text(_record(use)["resource_ref"]) for use in _list(result["new_uses"])
        ] == list(refs[4096:4097])
        assert_phase_delta(before, snapshot(runtime.ledger.engine))
        assert host.operations.read(operation.operation_id) == operation
        # The same recording RPC must still consume the old inputs plus its new read and fail.
        denied = await runtime.bus.dispatch(
            request(
                "operation/result/read",
                "rpc",
                {"project_id": "p", "operation_id": operation.operation_id},
            )
        )
        assert denied.error is not None
        assert denied.error.data["reason_code"] == "RESOURCE_SCOPE_USAGE_LIMIT"
        assert host.operations.read(operation.operation_id) == operation
    finally:
        runtime.close()


async def test_query_new_reads_keep_cap_and_recording_rpc_keeps_binding(tmp_path: Path) -> None:
    runtime, host, refs = await prepared(tmp_path)
    try:
        operation = sealed_failure(host, refs[:3], "operation:small")
        query = CompositeQuery(host, operation, refs[3:4])
        _registry(runtime).decorate("operation/result/read", lambda _: query.read)
        response = await runtime.bus.dispatch(
            request(
                "operation/result/read",
                "recorded-read",
                {"project_id": "p", "operation_id": operation.operation_id},
            )
        )
        assert response.error is None
        assert response.result is not None
        saved = host.operations.read(_text(response.result["operation_id"]))
        assert saved is not None
        assert saved.resource_uses is not None
        assert {use.resource_ref for use in saved.resource_uses} == set(refs[:4])
        _registry(runtime).decorate(
            "operation/result/read", lambda _: CompositeQuery(host, operation, refs[:4097]).read
        )
        denied = await runtime.bus.query(
            request(
                "operation/result/read",
                "too-wide-query",
                {"project_id": "p", "operation_id": operation.operation_id},
            )
        )
        assert denied.error is not None
        assert denied.error.data["reason_code"] == "RESOURCE_SCOPE_USAGE_LIMIT"
    finally:
        runtime.close()


@pytest.mark.parametrize(
    "kind,reason",
    (
        ("missing", "RESOURCE_SCOPE_UNKNOWN"),
        ("tampered", "OPERATION_RESOURCE_BINDING_INVALID"),
        ("foreign", None),
    ),
)
async def test_query_rejects_missing_tampered_and_foreign_sealed_bindings(
    tmp_path: Path, kind: str, reason: str | None
) -> None:
    from sqlalchemy import delete, update

    from thoth.adapters.storage.resource_scope_schema import operation_resource_bindings

    runtime = await setup(tmp_path, ControlledResearchModel(), source=False)
    try:
        host = research_host(runtime)
        operation = sealed_failure(host, (), "operation:integrity")
        with runtime.ledger.engine.begin() as connection:
            if kind == "missing":
                connection.execute(
                    delete(operation_resource_bindings).where(
                        operation_resource_bindings.c.operation_id == operation.operation_id
                    )
                )
            elif kind == "tampered":
                connection.execute(
                    update(operation_resource_bindings)
                    .where(operation_resource_bindings.c.operation_id == operation.operation_id)
                    .values(binding_digest="f" * 64)
                )
        before = snapshot(runtime.ledger.engine)
        denied = await runtime.bus.query(
            request(
                "operation/result/read",
                "integrity-query",
                {
                    "project_id": "elsewhere" if kind == "foreign" else "p",
                    "operation_id": operation.operation_id,
                },
            )
        )
        assert denied.error is not None
        if reason:
            assert denied.error.data["reason_code"] == reason
        assert "controlled failure" not in denied.error.message
        assert_phase_delta(before, snapshot(runtime.ledger.engine))
    finally:
        runtime.close()


async def test_query_revalidates_current_role_before_returning_historical_output(
    tmp_path: Path,
) -> None:
    from tests.integration.resource_scope_helpers import scope_harness

    from thoth.adapters.storage import SqliteGovernanceStore

    async with scope_harness(tmp_path) as h:
        connected = _http_value(
            await h.connect(
                "owner", "shared", {"owner_kind": "PROJECT", "visibility": "PROJECT_SHARED"}
            )
        )
        spans = _list(_http_value(await h.call("beta", "evidence/list", "visible", {}))["spans"])
        span = next(
            item
            for raw in spans
            if (item := _record(raw))["artifact_id"]
            == _record(connected["artifact"])["artifact_id"]
        )
        read = await h.call(
            "beta", "evidence/read", "old-output", {"span_id": _text(span["span_id"])}
        )
        operation_id = _text(_record(_record(read.json())["result"])["operation_id"])
        payload = _record(request(
            "operation/result/read",
            "read-role",
            {"project_id": h.project, "operation_id": operation_id},
        ).model_dump(mode="json", by_alias=True))
        _record(_record(payload["params"])["_meta"])["dataScope"] = {"workstream": "beta"}
        headers = {"authorization": f"Bearer {h.tokens['beta']}"}
        allowed = await h.client.post("/rpc/query", json=payload, headers=headers)
        assert "error" not in _record(allowed.json())
        governance = SqliteGovernanceStore(h.runtime.ledger.engine)
        role = next(
            role for role in governance.list_roles(h.project) if role.actor_id == h.actors["beta"]
        )
        governance.revoke_role(
            h.project, role.role_assignment_id, revoked_at=role.created_at.isoformat()
        )
        before = snapshot(h.runtime.ledger.engine)
        denied = await h.client.post("/rpc/query", json=payload, headers=headers)
        assert "error" in _record(denied.json()) and "owner-private-marker" not in denied.text
        assert_phase_delta(before, snapshot(h.runtime.ledger.engine))
