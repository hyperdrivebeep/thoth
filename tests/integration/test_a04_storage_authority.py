from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Event, Lock, get_ident
from typing import Any

import pytest
from sqlalchemy import func, select
from tests.integration.storage_coverage_helpers import (
    domain_snapshot,
    fail_after,
    prepare_project,
    request,
    value,
)

from thoth.adapters.runtime import SystemClock
from thoth.adapters.storage.action import SqliteActionStore
from thoth.adapters.storage.execution import SqliteExecutionStore
from thoth.adapters.storage.schema import action_authorizations, step_execution_attempts
from thoth.adapters.storage.sqlite import SqliteLedgerTransaction
from thoth.application.services.action_service import ActionService
from thoth.apps.runtime import AppRuntime, create_runtime
from thoth.domain.action_full import AuthorizationEnvelopeRecord
from thoth.domain.canonical import head_set_digest
from thoth.domain.execution_full import PlanExecutionRecord
from thoth.protocol.jsonrpc import JsonRpcResponse, RpcErrorCode


async def test_control_revision_race_returns_typed_rejection_not_internal_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    first, project, plan, _ = await prepare_r3(workspace)
    second = create_runtime(workspace)
    try:
        execution = value(await first.bus.dispatch(start_request(first, project, plan, "start")))[
            "execution"
        ]
        execution_id = str(execution["plan_execution_id"])
        original = SqliteExecutionStore.read_execution
        raced = False
        peer_snapshots: list[dict[str, tuple[str, ...]]] = []

        def race_read(
            store: SqliteExecutionStore,
            project_id: str,
            target: str,
        ) -> PlanExecutionRecord | None:
            nonlocal raced
            current = original(store, project_id, target)
            if target == execution_id and not raced:
                raced = True

                def peer_pause() -> JsonRpcResponse:
                    return asyncio.run(
                        second.bus.dispatch(
                            request(
                                "execution/pause",
                                "peer",
                                {
                                    "project_id": project,
                                    "plan_execution_id": execution_id,
                                    "expected_execution_revision": execution["revision"],
                                    "reason": "Peer pause",
                                },
                            )
                        )
                    )

                with ThreadPoolExecutor(max_workers=1) as pool:
                    assert pool.submit(peer_pause).result(timeout=20).error is None
                peer_snapshots.append(domain_snapshot(first.ledger.engine))
            return current

        with monkeypatch.context() as patch:
            patch.setattr(SqliteExecutionStore, "read_execution", race_read)
            response = await first.bus.dispatch(
                request(
                    "execution/pause",
                    "stale-pause",
                    {
                        "project_id": project,
                        "plan_execution_id": execution_id,
                        "expected_execution_revision": execution["revision"],
                        "reason": "Concurrent pause",
                    },
                )
            )
        assert raced and response.error is not None
        assert response.error.code == RpcErrorCode.DOMAIN_REJECTED
        assert domain_snapshot(first.ledger.engine) == peer_snapshots[0]
    finally:
        first.close()
        second.close()


async def test_expiry_between_frontier_and_consumption_has_no_partial_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, project, plan, approval = await prepare_r3(tmp_path / "workspace")
    before = domain_snapshot(runtime.ledger.engine)
    expires = datetime.fromisoformat(str(approval["expires_at"]).replace("Z", "+00:00"))
    original = ActionService.consume_authorization

    def expire_then_consume(
        service: ActionService,
        current: AuthorizationEnvelopeRecord,
        *,
        exact_scope_digest: str,
    ) -> AuthorizationEnvelopeRecord:
        def expired_now(clock: SystemClock) -> datetime:
            del clock
            return expires

        monkeypatch.setattr(SystemClock, "now", expired_now)
        return original(service, current, exact_scope_digest=exact_scope_digest)

    try:
        monkeypatch.setattr(ActionService, "consume_authorization", expire_then_consume)
        response = await runtime.bus.dispatch(start_request(runtime, project, plan, "expire"))
        assert response.error is not None and response.error.code == RpcErrorCode.DOMAIN_REJECTED
        assert domain_snapshot(runtime.ledger.engine) == before
    finally:
        runtime.close()


@pytest.mark.parametrize("vote_index", [0, -1])
async def test_revoked_approval_owner_cannot_be_consumed(tmp_path: Path, vote_index: int) -> None:
    runtime, project, plan, approval = await prepare_r3(tmp_path / "workspace")
    try:
        current = value(
            await runtime.bus.dispatch(
                request(
                    "project/read",
                    "project-read",
                    {
                        "project_id": project,
                    },
                )
            )
        )
        role_ref = approval["decision_history"][vote_index]["role_assignment_ref"]
        value(
            await runtime.bus.dispatch(
                request(
                    "project/role/revoke",
                    "revoke",
                    {
                        "project_id": project,
                        "expected_revision": current["revision"],
                        "role_assignment_id": role_ref,
                    },
                )
            )
        )
        before = domain_snapshot(runtime.ledger.engine)
        response = await runtime.bus.dispatch(start_request(runtime, project, plan, "revoked"))
        assert response.error is not None and response.error.code == RpcErrorCode.DOMAIN_REJECTED
        assert domain_snapshot(runtime.ledger.engine) == before
    finally:
        runtime.close()


async def test_latest_authorization_is_unambiguous_when_timestamps_tie(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fixed_now(clock: SystemClock) -> datetime:
        del clock
        return datetime(2026, 9, 5, tzinfo=UTC)

    monkeypatch.setattr(SystemClock, "now", fixed_now)
    runtime, project, plan, approval = await prepare_r3(tmp_path / "workspace")
    try:
        store = SqliteActionStore(runtime.ledger.engine)
        current = store.read_authorization(project, str(approval["authorization_id"]))
        assert current is not None and current.revision_digest == approval["revision_digest"]
        value(await runtime.bus.dispatch(start_request(runtime, project, plan, "same-time")))
        consumed = store.read_authorization(project, str(approval["authorization_id"]))
        assert consumed is not None and consumed.state == "CONSUMED"
    finally:
        runtime.close()


async def prepare_r3(
    workspace: Path, *, approve: bool = True
) -> tuple[AppRuntime, str, dict[str, Any], dict[str, Any]]:
    runtime, project = await prepare_project(workspace)
    owner = value(
        await runtime.bus.dispatch(
            request(
                "project/role/assign",
                "owner",
                {
                    "project_id": project,
                    "expected_revision": 0,
                    "actor_id": "human:owner",
                    "role": "project-owner",
                    "scope": "PROJECT",
                    "authority_tags": ["R3_ACTION_OWNER"],
                },
            )
        )
    )["role"]
    thread = value(
        await runtime.bus.dispatch(
            request(
                "thread/start",
                "thread",
                {
                    "project_id": project,
                    "thread_id": "thread:r3",
                    "problem": "Prepare a protected local record",
                },
            )
        )
    )
    action = value(
        await runtime.bus.dispatch(
            request(
                "action/create",
                "action",
                {
                    "project_id": project,
                    "object_id": thread["current_object_ids"][0],
                    "primary_purpose": "STATE_OR_DESIGN_CHANGE",
                    "specification": {
                        "description": "Synthetic protected configuration change",
                        "expected_observation_or_change": {
                            "description": "configuration target changes"
                        },
                        "effect_completeness_confirmed": True,
                        "stop_conditions": ["unexpected effect"],
                        "observability": "configuration receipt",
                        "effect_vector": {
                            "effect_completeness_confirmed": True,
                            "external_write": True,
                        },
                    },
                    "evidence_refs": [],
                },
            )
        )
    )["action"]
    plan = value(
        await runtime.bus.dispatch(
            request(
                "action/plan/compose",
                "plan",
                {
                    "project_id": project,
                    "object_id": thread["current_object_ids"][0],
                    "plan_id": "plan:r3",
                    "selected_action_refs": [action["action_id"]],
                    "step_candidates": [
                        {
                            "step_id": "step:r3",
                            "action_ref": action["action_id"],
                            "inputs": [],
                            "target_digests": ["b" * 64],
                            "output_contract": {"type": "configuration-receipt"},
                            "preconditions": [],
                            "stop_conditions": ["unexpected effect"],
                            "state": "READY",
                            "effect_vector": {
                                "effect_completeness_confirmed": True,
                                "external_write": True,
                            },
                        }
                    ],
                    "dependency_edges": [],
                },
            )
        )
    )["plan"]
    authorization = value(
        await runtime.bus.dispatch(
            request(
                "action/authorization/prepare",
                "prepare",
                {
                    "project_id": project,
                    "plan_id": plan["plan_id"],
                    "step_id": "step:r3",
                    "plan_revision_digest": plan["revision_digest"],
                    "predecessor_output_digests": [],
                    "target_baseline_digests": ["b" * 64],
                    "policy_version": "policy:current",
                },
            )
        )
    )["authorization"]
    if not approve:
        return (
            runtime,
            project,
            plan,
            {**authorization, "owner_role_ref": owner["role_assignment_id"]},
        )
    approved = await approve_required_roles(
        runtime, project, authorization, "human:owner", "approve"
    )
    return runtime, project, plan, approved


async def approve_required_roles(
    runtime: AppRuntime,
    project: str,
    authorization: dict[str, Any],
    actor: str,
    key: str,
) -> dict[str, Any]:
    current = value(
        await runtime.bus.dispatch(
            request(
                "action/authorization/read",
                key + "-read",
                {
                    "project_id": project,
                    "authorization_id": authorization["authorization_id"],
                },
            )
        )
    )["authorization"]
    roles = value(
        await runtime.bus.dispatch(
            request(
                "project/role/list",
                key + "-roles",
                {
                    "project_id": project,
                },
            )
        )
    )["roles"]
    approved_refs = {
        item.get("role_assignment_ref")
        for item in current["decision_history"]
        if item.get("decision") == "APPROVE" and not item.get("dissent")
    }
    approved_names = {
        item["role"]
        for item in roles
        if item["role_assignment_id"] in approved_refs and item["state"] == "ACTIVE"
    }
    for ordinal, name in enumerate(current["required_roles"]):
        if name in approved_names:
            continue
        role = next(
            (
                item
                for item in roles
                if item["role"] == name and item["actor_id"] == actor and item["state"] == "ACTIVE"
            ),
            None,
        )
        if role is None:
            project_value = value(
                await runtime.bus.dispatch(
                    request(
                        "project/read",
                        f"{key}-p-{ordinal}",
                        {
                            "project_id": project,
                        },
                    )
                )
            )
            role = value(
                await runtime.bus.dispatch(
                    request(
                        "project/role/assign",
                        f"{key}-r-{ordinal}",
                        {
                            "project_id": project,
                            "expected_revision": project_value["revision"],
                            "actor_id": actor,
                            "role": name,
                            "scope": "PROJECT",
                            "authority_tags": ["R3_ACTION_OWNER"],
                        },
                    )
                )
            )["role"]
        current = value(
            await runtime.bus.dispatch(
                request(
                    "action/authorization/decide",
                    f"{key}-d-{ordinal}",
                    {
                        "project_id": project,
                        "authorization_id": current["authorization_id"],
                        "decision": "APPROVE",
                        "actor_ref": actor,
                        "role_assignment_ref": role["role_assignment_id"],
                        "approved_digest": current["exact_scope_digest"],
                    },
                )
            )
        )["authorization"]
    assert current["state"] == "APPROVED"
    return current


def start_request(runtime: AppRuntime, project: str, plan: dict[str, Any], key: str):
    return request(
        "execution/start",
        key,
        {
            "project_id": project,
            "plan_id": plan["plan_id"],
            "plan_revision_digest": plan["revision_digest"],
            "execution_profile_ref": "local-metadata-only",
            "expected_working_head_digest": head_set_digest(runtime.ledger.read_heads(project)),
        },
    )


@pytest.mark.parametrize(
    ("owner", "method"),
    [
        (SqliteActionStore, "add_authorization"),
        (SqliteLedgerTransaction, "insert_receipt"),
        (SqliteExecutionStore, "add_attempt"),
        (SqliteExecutionStore, "append_audit"),
    ],
)
async def test_r3_consumption_and_attempt_fault_rollback_after_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    owner: type[object],
    method: str,
) -> None:
    workspace = tmp_path / "workspace"
    runtime, project, plan, _ = await prepare_r3(workspace)
    before = domain_snapshot(runtime.ledger.engine)
    try:
        with monkeypatch.context() as patch:
            fail_after(patch, owner, method)
            response = await runtime.bus.dispatch(start_request(runtime, project, plan, "start"))
            assert response.error is not None
        assert domain_snapshot(runtime.ledger.engine) == before
    finally:
        runtime.close()
    reopened = create_runtime(workspace)
    try:
        assert domain_snapshot(reopened.ledger.engine) == before
    finally:
        reopened.close()


async def test_single_use_r3_two_runtimes_commit_only_one_consumption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    first, project, plan, approval = await prepare_r3(workspace)
    second = create_runtime(workspace)
    first_request = start_request(first, project, plan, "first")
    second_request = start_request(second, project, plan, "second")
    rendezvous, lock = Event(), Lock()
    calls: dict[int, int] = {}
    second_reads = 0
    original = SqliteActionStore.list_authorizations

    def synchronize(
        store: SqliteActionStore,
        project_id: str,
        plan_id: str | None,
    ) -> tuple[AuthorizationEnvelopeRecord, ...]:
        nonlocal second_reads
        rows = original(store, project_id, plan_id)
        wait = False
        with lock:
            count = calls.get(get_ident(), 0) + 1
            calls[get_ident()] = count
            if count == 2:
                second_reads += 1
                wait = second_reads == 1
                if second_reads == 2:
                    rendezvous.set()
        # A serialized writer may correctly keep the peer outside this region.
        # The bounded wait is coordination, never an accepted DB/handler error.
        if wait:
            rendezvous.wait(timeout=0.5)
        return rows

    def dispatch(runtime: AppRuntime, candidate: Any) -> JsonRpcResponse:
        return asyncio.run(runtime.bus.dispatch(candidate))

    try:
        with monkeypatch.context() as patch:
            patch.setattr(SqliteActionStore, "list_authorizations", synchronize)
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [
                    pool.submit(dispatch, first, first_request),
                    pool.submit(dispatch, second, second_request),
                ]
                results = [future.result(timeout=20) for future in futures]
        assert sum(item.error is None for item in results) == 1
        failed = next(item for item in results if item.error is not None)
        assert failed.error is not None and failed.error.code == RpcErrorCode.DOMAIN_REJECTED
        with first.ledger.engine.connect() as connection:
            approvals = connection.execute(
                select(func.count())
                .select_from(action_authorizations)
                .where(action_authorizations.c.authorization_id == approval["authorization_id"])
            ).scalar_one()
            attempts = connection.execute(
                select(func.count())
                .select_from(step_execution_attempts)
                .where(step_execution_attempts.c.project_id == project)
            ).scalar_one()
        assert approvals == len(approval["decision_history"]) + 2  # Prepared, role votes, consumed.
        assert attempts == 1
    finally:
        first.close()
        second.close()
    reopened = create_runtime(workspace)
    try:
        current = SqliteActionStore(reopened.ledger.engine).read_authorization(
            project, str(approval["authorization_id"])
        )
        assert current is not None and current.state == "CONSUMED"
    finally:
        reopened.close()


async def test_delayed_approval_cannot_resurrect_consumed_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    first, project, plan, pending = await prepare_r3(workspace, approve=False)
    second = create_runtime(workspace)
    original = SqliteActionStore.read_authorization
    raced = False
    peer_snapshots: list[dict[str, tuple[str, ...]]] = []
    decision: dict[str, object] = {
        "project_id": project,
        "authorization_id": pending["authorization_id"],
        "decision": "APPROVE",
        "actor_ref": "human:owner",
        "role_assignment_ref": pending["owner_role_ref"],
        "approved_digest": pending["exact_scope_digest"],
    }

    def delayed_read(store: SqliteActionStore, project_id: str, target: str):
        nonlocal raced
        current = original(store, project_id, target)
        if target == pending["authorization_id"] and not raced:
            raced = True

            async def peer() -> None:
                await approve_required_roles(
                    second, project, pending, "human:owner", "peer-approve"
                )
                value(await second.bus.dispatch(start_request(second, project, plan, "peer-start")))

            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(lambda: asyncio.run(peer())).result(timeout=20)
            peer_snapshots.append(domain_snapshot(first.ledger.engine))
        return current

    try:
        with monkeypatch.context() as patch:
            patch.setattr(SqliteActionStore, "read_authorization", delayed_read)
            response = await first.bus.dispatch(
                request("action/authorization/decide", "delayed", decision)
            )
        assert raced and response.error is not None
        assert response.error.code == RpcErrorCode.DOMAIN_REJECTED
        assert domain_snapshot(first.ledger.engine) == peer_snapshots[0]
        held = value(await first.bus.dispatch(start_request(first, project, plan, "second-start")))
        assert held["execution"]["state"] == "PAUSED_PROTECTED_BOUNDARY"
        assert held["attempts"] == []
        after = domain_snapshot(first.ledger.engine)
        assert after["action_authorizations"] == peer_snapshots[0]["action_authorizations"]
        assert after["step_execution_attempts"] == peer_snapshots[0]["step_execution_attempts"]
        peer_snapshots.append(after)
    finally:
        first.close()
        second.close()
    reopened = create_runtime(workspace)
    try:
        current = original(
            SqliteActionStore(reopened.ledger.engine), project, str(pending["authorization_id"])
        )
        assert current is not None and current.state == "CONSUMED"
        assert domain_snapshot(reopened.ledger.engine) == peer_snapshots[-1]
    finally:
        reopened.close()


async def test_execution_latest_revision_survives_timestamp_ties(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fixed_now(clock: SystemClock) -> datetime:
        del clock
        return datetime(2026, 9, 5, tzinfo=UTC)

    monkeypatch.setattr(SystemClock, "now", fixed_now)
    runtime, project, plan, _ = await prepare_r3(tmp_path / "workspace")
    try:
        started = value(
            await runtime.bus.dispatch(start_request(runtime, project, plan, "tied-start"))
        )
        execution = started["execution"]
        store = SqliteExecutionStore(runtime.ledger.engine)
        latest = store.read_execution(project, str(execution["plan_execution_id"]))
        assert latest is not None and latest.revision_digest == execution["revision_digest"]
        paused = value(
            await runtime.bus.dispatch(
                request(
                    "execution/pause",
                    "tied-pause",
                    {
                        "project_id": project,
                        "plan_execution_id": execution["plan_execution_id"],
                        "expected_execution_revision": execution["revision"],
                        "reason": "pause at same time",
                    },
                )
            )
        )["execution"]
        latest = store.read_execution(project, str(execution["plan_execution_id"]))
        assert latest is not None and latest.revision_digest == paused["revision_digest"]
        assert store.list_executions(project) == (latest,)
        for attempt in store.list_attempts(project, latest.plan_execution_id):
            assert store.read_attempt(project, attempt.attempt_id) == attempt
    finally:
        runtime.close()


async def test_r3_requires_every_nonfungible_role_before_executable_frontier(
    tmp_path: Path,
) -> None:
    runtime, project, plan, pending = await prepare_r3(tmp_path / "workspace", approve=False)
    try:
        assert len(pending["required_roles"]) > 1
        decision = value(
            await runtime.bus.dispatch(
                request(
                    "action/authorization/decide",
                    "one-role",
                    {
                        "project_id": project,
                        "authorization_id": pending["authorization_id"],
                        "decision": "APPROVE",
                        "actor_ref": "human:owner",
                        "role_assignment_ref": pending["owner_role_ref"],
                        "approved_digest": pending["exact_scope_digest"],
                    },
                )
            )
        )["authorization"]
        assert decision["state"] == "PENDING"
        started = value(
            await runtime.bus.dispatch(start_request(runtime, project, plan, "incomplete-roles"))
        )
        assert started["execution"]["state"] == "PAUSED_PROTECTED_BOUNDARY"
        assert started["attempts"] == []
    finally:
        runtime.close()
