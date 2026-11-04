"""Receipt bundle, verify and audit reads publish their derived DAG cache with their result.

The cache sync is one unit: a fault after any cache write leaves no cache rows at all. A fault after
the sync (writing the bundle or the verification) leaves a complete, consistent cache and no bundle
or verification. A retry always produces a valid bundle.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue
from sqlalchemy import Engine
from tests.atomicity.harness import (
    Snapshot,
    assert_phase_delta,
    failed_command_allowances,
    snapshot,
)
from tests.integration.scoped_runtime import create_runtime
from tests.integration.storage_coverage_helpers import request, value

from thoth.adapters.storage.receipt_dag import SqliteReceiptDagStore
from thoth.apps.runtime import AppRuntime
from thoth.domain.canonical import head_set_digest

CACHE_TABLES = (
    "receipt_dag_nodes",
    "receipt_dag_edges",
    "receipt_dag_manifests",
    "receipt_dag_bundles",
    "receipt_dag_verifications",
)
PROJECT = "project:receipt-cache"
EVERYTHING: dict[str, object] = {"project_id": PROJECT, "receipt_ids": []}


async def prepare(workspace: Path) -> AppRuntime:
    runtime = create_runtime(workspace)
    created: dict[str, object] = {
        "project_id": PROJECT,
        "name": "Receipts",
        "cutoff_at": "2026-08-31T00:00:00Z",
    }
    value(await runtime.bus.dispatch(request("project/create", "project", created)))
    thread: dict[str, object] = {
        "project_id": PROJECT,
        "thread_id": "thread:receipt-cache",
        "problem": "Seal receipts safely",
        "scope": {"workstream": "integration"},
    }
    value(await runtime.bus.dispatch(request("thread/start", "thread", thread)))
    await seal(runtime, "seal-1")
    return runtime


async def seal(runtime: AppRuntime, key: str) -> None:
    heads = dict(runtime.ledger.read_heads(PROJECT))
    digest = head_set_digest(heads)
    body: dict[str, object] = {
        "project_id": PROJECT,
        "receipt_type": "TRANSITION",
        "claim_scopes": ["TRANSITION_RECORDED", "ARTIFACT_INTEGRITY"],
        "subject_refs": list(heads.values()),
        "before_head_set_digest": digest,
        "after_head_set_digest": digest,
        "actor_or_agent_ref": "agent:receipt-cache",
        "evidence_refs": [],
        "policy_version": "receipt:1",
        "model_versions": [],
        "tool_versions": ["thoth:test"],
    }
    value(await runtime.bus.dispatch(request("receipt/seal", key, body)))


def cache_rows(engine: Engine) -> dict[str, int]:
    state = snapshot(engine)
    return {table: len(state[table]) for table in CACHE_TABLES}


def receipt_reads(runtime: AppRuntime) -> tuple[str, ...]:
    """The scoped DAG view reads every receipt it shows, and a failed command keeps that record."""
    return tuple("receipt:" + r.receipt_digest for r in runtime.ledger.read_receipts(PROJECT))


def without_cache(state: Snapshot) -> Snapshot:
    return {table: rows for table, rows in state.items() if table not in CACHE_TABLES}


def assert_cache_is_consistent(engine: Engine) -> None:
    rows = cache_rows(engine)
    assert rows["receipt_dag_nodes"] >= 2
    assert rows["receipt_dag_edges"] == rows["receipt_dag_nodes"] - 1
    assert rows["receipt_dag_manifests"] == 1


def fail_cache_write(patch: pytest.MonkeyPatch, method: str) -> dict[str, int]:
    calls = {"n": 0}

    def fail(self: SqliteReceiptDagStore, record: object) -> None:
        calls["n"] += 1
        raise RuntimeError("injected receipt cache fault at " + method)

    patch.setattr(SqliteReceiptDagStore, method, fail)
    return calls


@pytest.mark.parametrize("method", ["put_node", "put_edge", "put_manifest"])
async def test_bundle_create_fault_leaves_no_partial_cache_and_retry_completes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    runtime = await prepare(tmp_path / "workspace")
    try:
        await seal(runtime, "seal-2")  # several receipts, so nodes and PRECEDES edges exist
        command = request("receipt/bundle/create", "bundle-fault", EVERYTHING)
        before = snapshot(runtime.ledger.engine)
        with monkeypatch.context() as patch:
            calls = fail_cache_write(patch, method)
            result = await runtime.bus.dispatch(command)
        assert result.error is not None, "the injected fault must surface"
        assert calls["n"] >= 1, "the fault point was reached"
        allowed = failed_command_allowances(runtime.ledger.engine, command)
        assert_phase_delta(before, snapshot(runtime.ledger.engine), allowed)
        assert all(count == 0 for count in cache_rows(runtime.ledger.engine).values())

        retry = request("receipt/bundle/create", "bundle-retry", EVERYTHING)
        created = value(await runtime.bus.dispatch(retry))
        bundle = cast(dict[str, JsonValue], created["bundle"])
        verify: dict[str, object] = {"project_id": PROJECT, "bundle_id": bundle["record_id"]}
        verified = value(await runtime.bus.dispatch(request("receipt/bundle/verify", "v", verify)))
        assert verified["integrity_state"] == "VALID"
        rows = cache_rows(runtime.ledger.engine)
        assert_cache_is_consistent(runtime.ledger.engine)
        assert rows["receipt_dag_bundles"] == 1
    finally:
        runtime.close()


async def test_bundle_create_fault_after_the_sync_leaves_a_complete_cache_and_no_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = await prepare(tmp_path / "workspace")
    try:
        await seal(runtime, "seal-2")
        command = request("receipt/bundle/create", "bundle-fault", EVERYTHING)
        before = snapshot(runtime.ledger.engine)
        with monkeypatch.context() as patch:
            calls = fail_cache_write(patch, "put_bundle")
            result = await runtime.bus.dispatch(command)
        assert result.error is not None and calls["n"] == 1
        allowed = failed_command_allowances(
            runtime.ledger.engine, command, expected_reads=receipt_reads(runtime)
        )
        assert_phase_delta(
            without_cache(before), without_cache(snapshot(runtime.ledger.engine)), allowed
        )
        assert_cache_is_consistent(runtime.ledger.engine)
        assert cache_rows(runtime.ledger.engine)["receipt_dag_bundles"] == 0
        retry = request("receipt/bundle/create", "bundle-retry", EVERYTHING)
        created = value(await runtime.bus.dispatch(retry))
        bundle = cast(dict[str, JsonValue], created["bundle"])
        verify: dict[str, object] = {"project_id": PROJECT, "bundle_id": bundle["record_id"]}
        verified = value(await runtime.bus.dispatch(request("receipt/bundle/verify", "v", verify)))
        assert verified["integrity_state"] == "VALID"
        assert cache_rows(runtime.ledger.engine)["receipt_dag_bundles"] == 1
    finally:
        runtime.close()


async def test_bundle_verify_fault_after_the_sync_writes_no_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = await prepare(tmp_path / "workspace")
    try:
        created = value(
            await runtime.bus.dispatch(request("receipt/bundle/create", "bundle", EVERYTHING))
        )
        bundle_id = cast(dict[str, JsonValue], created["bundle"])["record_id"]
        await seal(runtime, "seal-2")  # verify re-syncs: it rewrites the manifest and adds a node
        body: dict[str, object] = {"project_id": PROJECT, "bundle_id": bundle_id}
        command = request("receipt/bundle/verify", "verify-fault", body)
        before = snapshot(runtime.ledger.engine)
        with monkeypatch.context() as patch:
            calls = fail_cache_write(patch, "put_verification")
            result = await runtime.bus.dispatch(command)
        assert result.error is not None and calls["n"] == 1
        allowed = failed_command_allowances(
            runtime.ledger.engine, command, expected_reads=receipt_reads(runtime)
        )
        assert_phase_delta(
            without_cache(before), without_cache(snapshot(runtime.ledger.engine)), allowed
        )
        assert_cache_is_consistent(runtime.ledger.engine)
        assert cache_rows(runtime.ledger.engine)["receipt_dag_verifications"] == 0
        retried = value(await runtime.bus.dispatch(request("receipt/bundle/verify", "retry", body)))
        assert retried["integrity_state"] == "VALID"
    finally:
        runtime.close()


@pytest.mark.parametrize("method", ["put_edge", "put_manifest"])
async def test_audit_read_fault_leaves_no_partial_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    runtime = await prepare(tmp_path / "workspace")
    try:
        await seal(runtime, "seal-2")
        command = request("receipt/audit/read", "audit-fault", {"project_id": PROJECT})
        before = snapshot(runtime.ledger.engine)
        with monkeypatch.context() as patch:
            calls = fail_cache_write(patch, method)
            result = await runtime.bus.dispatch(command)
        assert result.error is not None and calls["n"] >= 1
        allowed = failed_command_allowances(runtime.ledger.engine, command)
        assert_phase_delta(before, snapshot(runtime.ledger.engine), allowed)
        assert all(count == 0 for count in cache_rows(runtime.ledger.engine).values())
        retry = request("receipt/audit/read", "audit-retry", {"project_id": PROJECT})
        dag = cast(dict[str, JsonValue], value(await runtime.bus.dispatch(retry))["dag"])
        assert len(cast(list[object], dag["nodes"])) >= 2
    finally:
        runtime.close()
