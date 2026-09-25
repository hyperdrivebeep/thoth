from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from sqlalchemy import select
from tests.integration.storage_coverage_helpers import (
    domain_snapshot,
    fail_after,
    prepare_project,
    request,
    value,
)

from thoth.adapters.storage import SqliteGovernanceStore, SqliteProjectStore
from thoth.adapters.storage.governance_history import SqliteGovernanceHistory
from thoth.adapters.storage.schema import events, project_policies
from thoth.apps.runtime import AppRuntime, create_runtime
from thoth.protocol.jsonrpc import JsonRpcResponse, RpcErrorCode


@pytest.mark.parametrize(
    "owner,method",
    [
        (SqliteProjectStore, "create"),
        (SqliteGovernanceStore, "put_policy"),
    ],
)
async def test_create_post_write_fault_has_no_project_or_policy_after_reopen(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    owner: type[object],
    method: str,
) -> None:
    workspace = tmp_path / "create"
    runtime = create_runtime(workspace)
    before = domain_snapshot(runtime.ledger.engine)
    project_id = "project:a13-atomic"
    payload: dict[str, object] = {
        "project_id": project_id,
        "name": "atomic",
        "cutoff_at": "2026-09-05T00:00:00Z",
    }
    try:
        with monkeypatch.context() as patch:
            fail_after(patch, owner, method)
            response = await runtime.bus.dispatch(request("project/create", "fault", payload))
        assert response.error is not None
        assert domain_snapshot(runtime.ledger.engine) == before
    finally:
        runtime.close()
    reopened = create_runtime(workspace)
    try:
        assert domain_snapshot(reopened.ledger.engine) == before
        value(await reopened.bus.dispatch(request("project/create", "retry", payload)))
        assert SqliteProjectStore(reopened.ledger.engine).read(project_id) is not None
        assert SqliteGovernanceStore(reopened.ledger.engine).read_policy(project_id)
    finally:
        reopened.close()


@pytest.mark.parametrize(
    "method,store_method,extra",
    [
        ("project/policy/update", "put_policy", {"payload": {"external_write": False}}),
        (
            "project/role/assign",
            "create_role",
            {
                "actor_id": "human:reviewer",
                "role": "reviewer",
                "scope": "WORKSTREAM:alpha",
            },
        ),
        ("project/role/revoke", "revoke_role", {}),
        (
            "project/reference/import",
            "add_reference",
            {
                "origin_project_id": "project:external",
                "origin_revision": "revision:external",
                "rights_status": "PERMITTED",
                "scope": "PROJECT",
            },
        ),
    ],
)
@pytest.mark.parametrize("fault_at", ["governance_write", "project_write"])
async def test_governance_post_write_fault_rolls_back_all_existing_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    store_method: str,
    extra: dict[str, object],
    fault_at: str,
) -> None:
    workspace = tmp_path / "governance"
    runtime, project = await prepare_project(workspace)
    expected_revision = 1 if method == "project/role/revoke" else 0
    payload: dict[str, object] = {
        "project_id": project,
        "expected_revision": expected_revision,
        **extra,
    }
    if method == "project/role/revoke":
        assigned = value(
            await runtime.bus.dispatch(
                request(
                    "project/role/assign",
                    "seed-role",
                    {
                        "project_id": project,
                        "expected_revision": 0,
                        "actor_id": "human:reviewer",
                        "role": "reviewer",
                    },
                )
            )
        )
        payload.update(
            expected_revision=1, role_assignment_id=assigned["role"]["role_assignment_id"]
        )
    before = domain_snapshot(runtime.ledger.engine)
    hits: list[str] = []
    try:
        with monkeypatch.context() as patch:
            if fault_at == "governance_write":
                hits = fail_after(patch, SqliteGovernanceStore, store_method)
            else:
                hits = fail_after(patch, SqliteProjectStore, "update")
            response = await runtime.bus.dispatch(request(method, "fault", payload))
        assert response.error is not None
        if method == "project/policy/update":
            assert hits == [store_method if fault_at == "governance_write" else "update"]
        assert domain_snapshot(runtime.ledger.engine) == before
    finally:
        runtime.close()
    reopened = create_runtime(workspace)
    try:
        assert domain_snapshot(reopened.ledger.engine) == before
        success = value(await reopened.bus.dispatch(request(method, "retry", payload)))
        assert success["revision"] == expected_revision + 1
        after = domain_snapshot(reopened.ledger.engine)
        assert value(await reopened.bus.dispatch(request(method, "retry", payload))) == success
        assert domain_snapshot(reopened.ledger.engine) == after
    finally:
        reopened.close()
    persisted = create_runtime(workspace)
    try:
        assert domain_snapshot(persisted.ledger.engine) == after
    finally:
        persisted.close()


async def test_two_runtime_policy_cas_has_one_winner_and_no_orphan_policy(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "race"
    first, project = await prepare_project(workspace)
    second = create_runtime(workspace)
    barrier = Barrier(2)

    def dispatch(runtime: AppRuntime, key: str) -> JsonRpcResponse:
        barrier.wait(timeout=10)
        return asyncio.run(
            runtime.bus.dispatch(
                request(
                    "project/policy/update",
                    key,
                    {
                        "project_id": project,
                        "expected_revision": 0,
                        "payload": {"external_write": False, "label": key},
                    },
                )
            )
        )

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(dispatch, runtime, key)
                for runtime, key in ((first, "left"), (second, "right"))
            ]
            results = [future.result(timeout=20) for future in futures]
        assert sum(result.error is None for result in results) == 1
        loser = next(result for result in results if result.error is not None)
        assert loser.error is not None
        assert loser.error.code == RpcErrorCode.DOMAIN_REJECTED
        winner = value(next(result for result in results if result.error is None))
        current = SqliteProjectStore(first.ledger.engine).read(project)
        assert current is not None and current.revision == 1
        assert current.policy_binding_ref == winner["policy"]["policy_id"]
        with first.ledger.engine.connect() as connection:
            policies = (
                connection.execute(
                    select(project_policies).where(
                        project_policies.c.project_id == project,
                    )
                )
                .mappings()
                .all()
            )
        assert len(policies) == 2
        assert {row["version"] for row in policies} == {1, 2}
        after = domain_snapshot(first.ledger.engine)
    finally:
        first.close()
        second.close()
    reopened = create_runtime(workspace)
    try:
        assert domain_snapshot(reopened.ledger.engine) == after
    finally:
        reopened.close()


async def test_policy_content_can_repeat_across_new_versions_without_replaying_publication(
    tmp_path: Path,
) -> None:
    runtime, project = await prepare_project(tmp_path / "repeat-policy")

    async def update(key: str, revision: int, label: str) -> JsonRpcResponse:
        return await runtime.bus.dispatch(
            request(
                "project/policy/update",
                key,
                {
                    "project_id": project,
                    "expected_revision": revision,
                    "payload": {"label": label},
                },
            )
        )

    def rows() -> list[dict[str, object]]:
        with runtime.ledger.engine.connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    select(project_policies)
                    .where(project_policies.c.project_id == project)
                    .order_by(project_policies.c.version)
                ).mappings()
            ]

    def notifications() -> int:
        with runtime.ledger.engine.connect() as connection:
            return len(
                connection.execute(
                    select(events.c.event_id).where(
                        events.c.project_id == project,
                        events.c.event_type == "project/policy/updated",
                    )
                ).all()
            )

    try:
        first = value(await update("policy-a-first", 0, "A"))
        after_first = rows()
        assert value(await update("policy-a-first", 0, "A")) == first
        assert rows() == after_first
        assert notifications() == 1

        stale = await update("policy-stale", 0, "A")
        assert stale.error is not None and stale.error.code == RpcErrorCode.DOMAIN_REJECTED
        assert rows() == after_first

        second = value(await update("policy-b", 1, "B"))
        third = value(await update("policy-a-again", 2, "A"))
        published = rows()
        assert [row["version"] for row in published] == [1, 2, 3, 4]
        assert len({row["policy_id"] for row in published}) == 4
        assert published[1]["policy_digest"] == published[3]["policy_digest"]
        assert published[1]["payload_json"] == published[3]["payload_json"]
        assert published[2]["policy_digest"] != published[3]["policy_digest"]
        assert first["policy"]["policy_id"] == published[1]["policy_id"]
        assert second["policy"]["policy_id"] == published[2]["policy_id"]
        assert third["policy"]["policy_id"] == published[3]["policy_id"]
        current = SqliteProjectStore(runtime.ledger.engine).read(project)
        assert current is not None and current.revision == 3
        assert current.policy_binding_ref == published[3]["policy_id"]
        assert notifications() == 3
        history = SqliteGovernanceHistory(runtime.ledger.engine).history(
            project, "PROJECT", project
        )
        assert len(history) == 4
        baseline = domain_snapshot(runtime.ledger.engine)
    finally:
        runtime.close()
    reopened = create_runtime(tmp_path / "repeat-policy")
    try:
        assert domain_snapshot(reopened.ledger.engine) == baseline
    finally:
        reopened.close()
