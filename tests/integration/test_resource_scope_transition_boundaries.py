"""104-V: HTTP scope transitions and real post-write expiry retain outer UoW atomicity."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import MethodType
from typing import Any, cast

import pytest
from sqlalchemy import Engine, create_engine, delete, select
from tests.integration.resource_scope_helpers import ScopeHarness, denial, scope_harness, value
from tests.integration.test_a02_autonomous_acquisition import StaticModelResolver
from tests.integration.test_m5_revision_control_atomicity import NoModel

from thoth.adapters.storage.auth import SqliteAuthSessionStore
from thoth.adapters.storage.resource_scope import SqliteResourceScopeStore
from thoth.adapters.storage.resource_scope_schema import (
    resource_scope_heads,
    resource_scope_history,
    resource_scope_receipts,
)
from thoth.adapters.storage.schema import operations
from thoth.adapters.storage.transaction import read_connection
from thoth.application.commands.resource_scopes import ResourceScopeHandlers
from thoth.application.services.resource_scope_service import ResourceScopeService
from thoth.domain.auth import current_authenticated_actor
from thoth.domain.resource_scope import ResourceScopeReceipt, ResourceScopeRecord
from thoth.ports.model import ModelPort
from thoth.ports.runtime import ClockPort
from thoth.protocol.registry import MethodRegistry

OUT = Path(__file__).resolve().parents[2] / "outputs/refactor-step104-20261016"
BOOKKEEPING = {
    "operations",
    "idempotency_keys",
    "events",
    "checkpoints",
    "operation_resource_bindings",
}
Snapshot = dict[str, tuple[str, ...]]


def snapshot(engine: Engine) -> Snapshot:
    result: Snapshot = {}
    with engine.connect() as connection:
        names = (
            connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            .scalars()
            .all()
        )
        for name in names:
            name = str(name)
            if name in BOOKKEEPING:
                continue
            quoted = '"' + name.replace('"', '""') + '"'
            rows = connection.exec_driver_sql("SELECT * FROM " + quoted).mappings()
            result[name] = tuple(
                sorted(json.dumps(dict(row), sort_keys=True, default=str) for row in rows)
            )
    assert {
        "resource_scope_heads",
        "resource_scope_history",
        "resource_scope_receipts",
        "structural_fts",
    } <= result.keys()
    return result


def reopened(h: ScopeHarness, expected: Snapshot) -> None:
    engine = create_engine(h.runtime.ledger.engine.url)
    try:
        assert snapshot(engine) == expected
    finally:
        engine.dispose()


def file_hashes(workspace: Path) -> dict[str, str]:
    return {
        p.relative_to(workspace).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for folder in (workspace / "inbox", workspace / "objects")
        if folder.exists()
        for p in folder.rglob("*")
        if p.is_file()
    }


def service(h: ScopeHarness, method: str) -> ResourceScopeService:
    registry = vars(h.runtime.bus)["_registry"]
    assert isinstance(registry, MethodRegistry)
    bound = registry.resolve(method)
    assert isinstance(bound, MethodType) and isinstance(bound.__self__, ResourceScopeHandlers)
    scopes = vars(bound.__self__)["_scopes"]
    assert isinstance(scopes, ResourceScopeService)
    return scopes


async def source(h: ScopeHarness, key: str, *, legacy: bool = False) -> str:
    response = value(
        await h.connect(
            "alpha",
            key,
            {"owner_kind": "WORKSTREAM", "owner_workstream": "alpha", "visibility": "WORKSTREAM"},
        )
    )
    resource = str(response["artifact"]["artifact_id"])
    if legacy:
        # Same isolated fixture preparation as the existing legacy-transfer test.
        with h.runtime.ledger.engine.begin() as connection:
            for table in (resource_scope_receipts, resource_scope_heads, resource_scope_history):
                connection.execute(
                    delete(table).where(
                        table.c.project_id == h.project, table.c.resource_ref == resource
                    )
                )
        assert SqliteResourceScopeStore(h.runtime.ledger.engine).read(h.project, resource) is None
    return resource


def assign_body(resource: str) -> dict[str, object]:
    return {
        "resource_ref": resource,
        "expected_revision": 0,
        "scope": {
            "owner_kind": "WORKSTREAM",
            "owner_workstream": "alpha",
            "visibility": "WORKSTREAM",
        },
        "reason": "104-V reviewed legacy assignment",
    }


@dataclass
class Probe:
    writes: list[ResourceScopeRecord] = field(default_factory=list)
    receipts: list[ResourceScopeReceipt] = field(default_factory=list)
    hits: int = 0
    expiry: datetime | None = None
    session_was_active: bool = False


def install(
    patch: pytest.MonkeyPatch,
    h: ScopeHarness,
    scopes: ResourceScopeService,
    resource: str,
    event: str,
    *,
    mode: str,
) -> Probe:
    probe = Probe()
    original = SqliteResourceScopeStore.append
    clock = cast(ClockPort, vars(scopes)["_clock"])
    original_now = clock.now

    def now() -> datetime:
        return original_now() if probe.expiry is None else probe.expiry

    if mode == "expiry":
        patch.setattr(clock, "now", now)

    def append(
        store: SqliteResourceScopeStore,
        record: ResourceScopeRecord,
        receipt: ResourceScopeReceipt,
        *,
        expected_revision: int,
    ) -> None:
        original(store, record, receipt, expected_revision=expected_revision)
        if record.project_id != h.project or record.resource_ref != resource:
            return
        assert record.event_kind == event
        with read_connection(h.runtime.ledger.engine) as connection:
            assert connection.in_transaction(), "service outer transaction must still be active"
        stored = store.read(h.project, resource)
        assert stored is not None and stored.model_dump(mode="json") == record.model_dump(
            mode="json"
        )
        probe.writes.append(record)
        probe.receipts.append(receipt)
        if mode == "fault":
            probe.hits += 1
            raise RuntimeError("104-V after completed scope append")
        if mode == "expiry":
            actor = current_authenticated_actor()
            assert actor is not None and actor.actor_id == h.actors["owner"]
            session = SqliteAuthSessionStore(h.runtime.ledger.engine).read(actor.session_id)
            assert session is not None and session.state == "ACTIVE"
            assert session.project_id == h.project and session.expires_at > original_now()
            probe.session_was_active = True
            probe.expiry = session.expires_at
            probe.hits += 1
            # Return normally. The unpatched admit -> _actor check must reject expiry.

    patch.setattr(SqliteResourceScopeStore, "append", append)
    return probe


def save(name: str, before: Snapshot, after: Snapshot, probe: Probe, **detail: object) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    report = {
        "changed_tables": [k for k in before if before[k] != after[k]],
        "table_count": len(before),
        "fault_hits": probe.hits,
        "writes": [
            {
                "resource_ref": r.resource_ref,
                "revision": r.revision,
                "event_kind": r.event_kind,
                "record_digest": r.record_digest,
            }
            for r in probe.writes
        ],
        "before_sha256": hashlib.sha256(json.dumps(before, sort_keys=True).encode()).hexdigest(),
        "after_sha256": hashlib.sha256(json.dumps(after, sort_keys=True).encode()).hexdigest(),
        **detail,
    }
    (OUT / ("v104-" + name + ".json")).write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n"
    )


def failed_bookkeeping(h: ScopeHarness, method: str, key: str, error: dict[str, Any]) -> None:
    with h.runtime.ledger.engine.connect() as connection:
        row = (
            connection.execute(
                select(operations).where(
                    operations.c.project_id == h.project,
                    operations.c.method == method,
                    operations.c.idempotency_key == key,
                )
            )
            .mappings()
            .one()
        )
    assert row["state"] == "FAILED" and row["result_json"] is None
    assert json.loads(row["error_json"]) == error


def assert_scope_delta(before: Snapshot, after: Snapshot, probe: Probe, *, assigned: bool) -> None:
    assert len(probe.writes) == len(probe.receipts) == 1
    record, receipt = probe.writes[0], probe.receipts[0]
    for name in before:
        if name not in {
            "resource_scope_heads",
            "resource_scope_history",
            "resource_scope_receipts",
        }:
            assert before[name] == after[name], name
    for name, model in [("resource_scope_history", record), ("resource_scope_receipts", receipt)]:
        assert set(before[name]).issubset(after[name])
        added = [json.loads(r) for r in set(after[name]) - set(before[name])]
        assert len(added) == 1 and json.loads(added[0]["content_json"]) == model.model_dump(
            mode="json"
        )
    added = [
        json.loads(r)
        for r in set(after["resource_scope_heads"]) - set(before["resource_scope_heads"])
    ]
    removed = [
        json.loads(r)
        for r in set(before["resource_scope_heads"]) - set(after["resource_scope_heads"])
    ]
    assert len(added) == 1 and len(removed) == (0 if assigned else 1)
    assert (
        added[0]["resource_ref"] == record.resource_ref
        and added[0]["record_digest"] == record.record_digest
    )


@pytest.mark.parametrize("operation", ["update", "revoke", "assign"])
async def test_scope_transition_post_append_rollback_and_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    model = NoModel()
    async with scope_harness(
        tmp_path, model_resolver=StaticModelResolver(cast(ModelPort, model))
    ) as h:
        resource = await source(h, "target", legacy=operation == "assign")
        actor = "owner" if operation == "assign" else "alpha"
        method = "project/source/scope/" + operation
        scopes = service(h, method)
        body: dict[str, object]
        if operation == "assign":
            body = assign_body(resource)
            event = "ASSIGNED"
        else:
            current = value(
                await h.call(
                    "alpha", "project/source/scope/read", "basis", {"resource_ref": resource}
                )
            )["scope"]
            granted = value(
                await h.call(
                    "alpha",
                    "project/source/scope/grant",
                    "grant",
                    {
                        "resource_ref": resource,
                        "expected_revision": current["revision"],
                        "grantee_kind": "ACTOR",
                        "grantee_ref": h.actors["beta"],
                        "reason": "104-V setup",
                    },
                )
            )["scope"]
            if operation == "revoke":
                body = {
                    "resource_ref": resource,
                    "expected_revision": granted["revision"],
                    "grant_id": granted["grants"][0]["grant_id"],
                    "reason": "104-V revoke",
                }
                event = "REVOKED"
            else:
                body = {
                    "resource_ref": resource,
                    "expected_revision": granted["revision"],
                    "scope": {
                        "owner_kind": "WORKSTREAM",
                        "owner_workstream": "alpha",
                        "visibility": "WORKSTREAM",
                    },
                    "reason": "104-V restrict",
                }
                event = "VISIBILITY_CHANGED"
        before = snapshot(h.runtime.ledger.engine)
        files = file_hashes(tmp_path)
        with monkeypatch.context() as patch:
            probe = install(patch, h, scopes, resource, event, mode="fault")
            failed = await h.call(actor, method, "fault", body)
            error = failed.json()["error"]
            assert error["code"] == -32603 and probe.hits == 1 and len(probe.writes) == 1
            after = snapshot(h.runtime.ledger.engine)
            save(operation + "-fault", before, after, probe, error_code=error["code"])
            assert after == before, "HOLD_PARTIAL_PUBLICATION: post-append transition"
            reopened(h, before)
            replay = await h.call(actor, method, "fault", body)
            assert replay.json()["error"] == error and probe.hits == 1 and len(probe.writes) == 1
            assert snapshot(h.runtime.ledger.engine) == before
        failed_bookkeeping(h, method, "fault", error)
        with monkeypatch.context() as patch:
            retry = install(patch, h, scopes, resource, event, mode="observe")
            response = value(await h.call(actor, method, "new-key", body))["scope"]
            after = snapshot(h.runtime.ledger.engine)
            assert_scope_delta(before, after, retry, assigned=operation == "assign")
            assert response == retry.writes[0].model_dump(mode="json")
            if operation != "assign":
                assert all(g["state"] == "REVOKED" for g in response["grants"])
            again = value(await h.call(actor, method, "new-key", body))["scope"]
            assert (
                again == response
                and len(retry.writes) == 1
                and snapshot(h.runtime.ledger.engine) == after
            )
        reopened(h, after)
        assert file_hashes(tmp_path) == files and model.calls == 0
        save(
            operation + "-retry",
            before,
            after,
            retry,
            same_key_error_replay=True,
            new_key_single_publication=True,
            successful_replay_no_writes=True,
            files_unchanged=True,
            model_calls=model.calls,
        )


async def test_http_assign_expiry_after_append_rolls_back_denied_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = NoModel()
    async with scope_harness(
        tmp_path, model_resolver=StaticModelResolver(cast(ModelPort, model))
    ) as h:
        method = "project/source/scope/assign"
        scopes = service(h, method)
        control = await source(h, "normal-control", legacy=True)
        assert (
            value(await h.call("owner", method, "control", assign_body(control)))["scope"][
                "revision"
            ]
            == 1
        )
        resource = await source(h, "denied-target", legacy=True)
        before = snapshot(h.runtime.ledger.engine)
        files = file_hashes(tmp_path)
        with monkeypatch.context() as patch:
            probe = install(patch, h, scopes, resource, "ASSIGNED", mode="expiry")
            rejected = await h.call("owner", method, "expired-after-write", assign_body(resource))
            denial(rejected, "RESOURCE_AUTHORITY_CHANGED")
            assert probe.hits == 1 and len(probe.writes) == 1 and probe.session_was_active
            after = snapshot(h.runtime.ledger.engine)
            save(
                "expiry-denial",
                before,
                after,
                probe,
                reason="RESOURCE_AUTHORITY_CHANGED",
                original_actor_check=True,
                session_active_before_clock_advance=True,
            )
            assert after == before, "HOLD_PARTIAL_PUBLICATION: actual post-append expiry denial"
            reopened(h, before)
            assert file_hashes(tmp_path) == files and model.calls == 0
            failed_bookkeeping(h, method, "expired-after-write", rejected.json()["error"])
        # No replay or recovery after reversing a synthetic clock is claimed.
