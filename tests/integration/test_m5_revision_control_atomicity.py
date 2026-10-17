"""102-V: generic ChangeSet wrapper UoW and per-control lineage publication.

Use normal registered RPCs. Do not call the root commit directly or duplicate strict restore
publication tests. The raw write runs before each fault; domain snapshots include control and
scope tables. No model is permitted. Four cases include same-key replay and fresh-key handling.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import MethodType
from typing import Any, cast

import pytest
from tests.integration.scoped_runtime import create_runtime
from tests.integration.storage_coverage_helpers import domain_snapshot, request, value
from tests.integration.test_a02_autonomous_acquisition import StaticModelResolver
from tests.integration.test_m5_improvement_handlers_atomicity import Snapshot, check_reopened

from thoth.adapters.storage.control_record import SqliteControlRecordStore
from thoth.adapters.storage.resource_scope import SqliteResourceScopeStore
from thoth.adapters.storage.transaction import read_connection
from thoth.application.commands.restore_proposals import RestoreProposalHandlers
from thoth.application.commands.revisions_full import RevisionFullHandlers
from thoth.application.services.baseline_service import BaselineService
from thoth.application.services.resource_scope_service import ResourceScopeService
from thoth.application.services.revision_service import CommitResult, RevisionCommitService
from thoth.apps.runtime import AppRuntime
from thoth.domain.canonical import head_set_digest
from thoth.domain.control_record import ControlRecord
from thoth.domain.revision import RevisionChangeSet
from thoth.ports.model import ModelPort
from thoth.protocol.jsonrpc import JsonRpcResponse
from thoth.protocol.registry import MethodRegistry

OUT = Path(__file__).resolve().parents[2] / "outputs/refactor-step102-20261015"
FAULT = "102-V after original storage write"


class NoModel:
    def __init__(self) -> None:
        self.calls = 0

    async def structured(self, *_args: Any, **_kwargs: Any) -> Any:
        self.calls += 1
        raise AssertionError("102-V must not call any model")


@dataclass
class Harness:
    runtime: AppRuntime
    project: str
    object_id: str
    heads: dict[str, str]
    content: dict[str, object]
    schema: str
    model: NoModel

    @property
    def key(self) -> str:
        return "DECISION_OBJECT:" + self.object_id

    async def call(self, method: str, key: str, data: dict[str, object]) -> JsonRpcResponse:
        return await self.runtime.bus.dispatch(
            request(method, key, {"project_id": self.project, **data})
        )

    async def propose(self, key: str) -> dict[str, Any]:
        return value(
            await self.call(
                "revision/propose",
                key,
                {
                    "aggregate_id": self.object_id,
                    "aggregate_type": "DECISION_OBJECT",
                    "parent_revision_digests": [self.heads[self.key]],
                    "candidate_content": {**self.content, "purpose_statement": key},
                    "reason": key,
                    "evidence_refs": [],
                    "actor_or_agent_ref": "agent:102-v",
                    "expected_head_digest": self.heads[self.key],
                    "candidate_schema_version": self.schema,
                },
            )
        )["proposal"]

    async def change(
        self, digest: str, key: str, heads: dict[str, str] | None = None
    ) -> dict[str, Any]:
        return value(
            await self.call(
                "revision/changeSet/create",
                key,
                {
                    "expected_head_set": self.heads if heads is None else heads,
                    "candidate_revision_digests": [digest],
                    "transition_reason": key,
                    "impact_policy_ref": "102-v",
                },
            )
        )["change_set"]

    async def validate(self, change: dict[str, Any], key: str) -> dict[str, Any]:
        return value(
            await self.call(
                "revision/changeSet/validate",
                key,
                {
                    "record_id": change["record_id"],
                    "expected_change_set_revision": change["version"],
                },
            )
        )

    def snapshot(self) -> Snapshot:
        return domain_snapshot(self.runtime.ledger.engine)


@asynccontextmanager
async def harness(path: Path) -> AsyncGenerator[Harness]:
    model = NoModel()
    runtime = create_runtime(path, model_resolver=StaticModelResolver(cast(ModelPort, model)))
    project = "project:102-v"
    try:
        value(
            await runtime.bus.dispatch(
                request(
                    "project/create",
                    "project",
                    {
                        "project_id": project,
                        "name": "102-V revision storage",
                        "cutoff_at": "2026-09-01T00:00:00Z",
                    },
                )
            )
        )
        thread = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "thread",
                    {
                        "project_id": project,
                        "thread_id": "thread:102-v",
                        "problem": "Revision storage fixture",
                        "scope": {"workstream": "revision"},
                    },
                )
            )
        )
        object_id = str(thread["current_object_ids"][0])
        heads = dict(runtime.ledger.read_heads(project))
        revision = runtime.ledger.read_revision_by_digest(
            project, heads["DECISION_OBJECT:" + object_id]
        )
        assert revision is not None
        snapshot = runtime.ledger.read_snapshot(revision.snapshot_id)
        assert snapshot is not None
        yield Harness(
            runtime, project, object_id, heads, snapshot.content, snapshot.schema_version, model
        )
        assert model.calls == 0
    finally:
        runtime.close()


def wrapper(h: Harness, method: str, legacy: str) -> RestoreProposalHandlers:
    registry = vars(h.runtime.bus).get("_registry")
    assert isinstance(registry, MethodRegistry)
    bound = registry.resolve(method)
    assert isinstance(bound, MethodType) and isinstance(bound.__self__, RestoreProposalHandlers)
    delegate = getattr(bound.__self__, legacy)
    assert isinstance(delegate, MethodType) and isinstance(delegate.__self__, RevisionFullHandlers)
    return bound.__self__


@dataclass
class Probe:
    enabled: bool = True
    hits: int = 0
    order: list[str] = field(default_factory=list)
    controls: list[ControlRecord] = field(default_factory=list)
    commits: list[CommitResult] = field(default_factory=list)
    lineage: list[str] = field(default_factory=list)


def in_transaction(h: Harness) -> None:
    with read_connection(h.runtime.ledger.engine) as connection:
        assert connection.in_transaction(), "registered outer transaction was not active"


def install(
    patch: pytest.MonkeyPatch,
    h: Harness,
    target: Callable[[ControlRecord], bool],
    *,
    fault_at: str = "control",
) -> Probe:
    probe = Probe()
    original_commit = RevisionCommitService.commit
    original_refresh = BaselineService.refresh
    original_append = SqliteControlRecordStore.append
    original_lineage = ResourceScopeService.record_control_lineage

    def commit(service: RevisionCommitService, changeset: RevisionChangeSet) -> CommitResult:
        in_transaction(h)
        result = original_commit(service, changeset)
        in_transaction(h)  # inner writer exited; the registered wrapper still owns the UoW.
        probe.commits.append(result)
        probe.order.append("commit:" + result.disposition.value)
        return result

    def refresh(service: BaselineService, **kwargs: Any) -> Any:
        in_transaction(h)
        result = original_refresh(service, **kwargs)
        assert kwargs["propose_candidates"] is False
        probe.order.append("baseline.refresh")
        return result

    def append(store: SqliteControlRecordStore, record: ControlRecord) -> None:
        original_append(store, record)
        if record.project_id != h.project or record.namespace != "REVISION":
            return
        probe.controls.append(record)
        probe.order.append(f"control:{record.record_type}:{record.state}")
        if fault_at == "control" and probe.enabled and target(record):
            probe.hits += 1
            raise RuntimeError(FAULT)

    def lineage(
        service: ResourceScopeService, project: str, digest: str, parents: tuple[str, ...]
    ) -> None:
        original_lineage(service, project, digest, parents)
        matching = [
            r for r in probe.controls if r.record_digest == digest and r.project_id == project
        ]
        if not matching:
            return
        in_transaction(h)
        scoped = SqliteResourceScopeStore(h.runtime.ledger.engine).read(
            project, "control:" + digest
        )
        assert scoped is not None and scoped.resource_ref == "control:" + digest
        probe.lineage.append(digest)
        probe.order.append("lineage:" + matching[0].record_type)
        if fault_at == "lineage" and probe.enabled and target(matching[0]):
            probe.hits += 1
            raise RuntimeError(FAULT)

    patch.setattr(RevisionCommitService, "commit", commit)
    patch.setattr(BaselineService, "refresh", refresh)
    patch.setattr(SqliteControlRecordStore, "append", append)
    patch.setattr(ResourceScopeService, "record_control_lineage", lineage)
    return probe


def control_rows(snapshot: Snapshot) -> dict[str, ControlRecord]:
    parsed = [
        ControlRecord.model_validate_json(json.loads(r)["content_json"])
        for r in snapshot["control_records"]
    ]
    return {r.control_revision_id: r for r in parsed}


def assert_added_controls(before: Snapshot, after: Snapshot, expected: list[ControlRecord]) -> None:
    old, new = control_rows(before), control_rows(after)
    assert old.keys() <= new.keys()
    assert {k: v.model_dump(mode="json") for k, v in old.items()} == {
        k: new[k].model_dump(mode="json") for k in old
    }
    assert {k: new[k].model_dump(mode="json") for k in new.keys() - old.keys()} == {
        r.control_revision_id: r.model_dump(mode="json") for r in expected
    }


def assert_new_control_scopes(
    h: Harness, before: Snapshot, after: Snapshot, controls: list[ControlRecord]
) -> None:
    references = {"control:" + row.record_digest for row in controls}
    for table in ("resource_scope_history", "resource_scope_heads", "resource_scope_receipts"):
        assert set(before[table]).issubset(after[table])
        added = [json.loads(row) for row in set(after[table]) - set(before[table])]
        assert len(added) == len(references)
        assert {row["resource_ref"] for row in added} == references
        assert all(row["project_id"] == h.project for row in added)


def save(name: str, before: Snapshot, after: Snapshot, probe: Probe, **details: object) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"v102-{name}.json").write_text(
        json.dumps(
            {
                "changed_tables": [k for k in before if before[k] != after[k]],
                "fault_hits": probe.hits,
                "order": probe.order,
                "control_writes": [
                    {
                        "namespace": r.namespace,
                        "record_type": r.record_type,
                        "record_id": r.record_id,
                        "state": r.state,
                        "version": r.version,
                    }
                    for r in probe.controls
                ],
                "lineage_digests": probe.lineage,
                "commit_dispositions": [r.disposition.value for r in probe.commits],
                **details,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )


async def rollback_and_replay(
    h: Harness, probe: Probe, method: str, body: dict[str, object], before: Snapshot, name: str
) -> None:
    failed = await h.call(method, "fault", body)
    assert failed.error is not None and probe.hits == 1
    after = h.snapshot()
    save(name + "-fault", before, after, probe)
    assert after == before, "HOLD_DEFECT: generic revision/lineage UoW partially published"
    check_reopened(h.runtime.ledger.engine, before)
    order = list(probe.order)
    replay = await h.call(method, "fault", body)
    assert replay.error == failed.error and probe.order == order
    assert h.snapshot() == before
    probe.enabled = False


def commit_body(
    h: Harness, change: dict[str, Any], validation: dict[str, Any]
) -> dict[str, object]:
    return {
        "record_id": change["record_id"],
        "validation_bundle_digest": validation["validation_bundle_digest"],
        "expected_head_set_digest": head_set_digest(h.heads),
    }


@pytest.mark.parametrize("branched", [False, True])
async def test_last_generic_commit_control_fault_rolls_back_the_registered_wrapper_uow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, branched: bool
) -> None:
    async with harness(tmp_path) as h:
        wrapper(h, "revision/changeSet/commit", "legacy_commit")
        wrapper(h, "revision/changeSet/validate", "legacy_validate")
        proposal = await h.propose("target")
        if branched:
            rival = await h.propose("rival")
            change = await h.change(rival["record_digest"], "rival-change")
            validation = await h.validate(change, "rival-validate")
            value(
                await h.call(
                    "revision/changeSet/commit", "rival-commit", commit_body(h, change, validation)
                )
            )
        target = await h.change(proposal["record_digest"], "target-change")
        checked = await h.validate(target, "target-validate")
        assert checked["state"] == "READY_TO_COMMIT"
        assert checked["checks"]["branch_preservation"] is branched
        body = commit_body(h, target, checked)
        before = h.snapshot()
        heads_before = dict(h.runtime.ledger.read_heads(h.project))
        revisions_before = h.runtime.ledger.read_revisions(
            h.project, "DECISION_OBJECT", h.object_id
        )
        receipts_before = h.runtime.ledger.read_receipts(h.project)
        state = "BRANCHED" if branched else "COMMITTED"
        disposition = "BRANCH" if branched else "FAST_FORWARD"
        with monkeypatch.context() as patch:
            probe = install(
                patch,
                h,
                lambda r: (
                    r.record_type == "CHANGE_SET"
                    and r.record_id == target["record_id"]
                    and r.state == state
                ),
            )
            await rollback_and_replay(
                h,
                probe,
                "revision/changeSet/commit",
                body,
                before,
                "branch" if branched else "fast-forward",
            )
            assert probe.order == [
                "commit:" + disposition,
                "baseline.refresh",
                "control:CHANGE_SET:" + state,
            ]
            assert len(probe.commits) == 1
            assert probe.controls[0].version == checked["change_set"]["version"] + 1
            mark = len(probe.controls)
            retried = value(await h.call("revision/changeSet/commit", "retry", body))
            assert len(probe.commits) == 2 and probe.commits[-1].disposition.value == disposition
            assert retried["change_set"]["state"] == state and retried["contains_restore"] is False
            assert_added_controls(before, h.snapshot(), probe.controls[mark:])
            assert probe.controls[-1].record_id == target["record_id"]
            assert probe.lineage == [probe.controls[-1].record_digest]
        heads_after = dict(h.runtime.ledger.read_heads(h.project))
        assert (heads_after == heads_before) is branched
        assert (
            len(h.runtime.ledger.read_revisions(h.project, "DECISION_OBJECT", h.object_id))
            == len(revisions_before) + 1
        )
        assert len(h.runtime.ledger.read_receipts(h.project)) == len(receipts_before) + 1
        if branched:
            assert len(retried["branch_revision_digests"]) == 1
            assert (
                h.runtime.ledger.read_revision_by_digest(
                    h.project, retried["branch_revision_digests"][0]
                )
                is not None
            )
        else:
            assert heads_after[h.key] != heads_before[h.key]
        check_reopened(h.runtime.ledger.engine, h.snapshot())
        save(
            "branch-retry" if branched else "fast-forward-retry",
            before,
            h.snapshot(),
            probe,
            actual_registered_wrapper=True,
            model_calls=h.model.calls,
        )


async def test_held_validation_conflict_fault_rolls_back_change_set_and_lineage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with harness(tmp_path) as h:
        wrapper(h, "revision/changeSet/validate", "legacy_validate")
        proposal = await h.propose("held-target")
        wrong_heads = {**h.heads, "DECISION_OBJECT:not-in-proposal": "f" * 64}
        change = await h.change(proposal["record_digest"], "held-change", wrong_heads)
        assert change["state"] == "STAGED"
        body = {"record_id": change["record_id"], "expected_change_set_revision": change["version"]}
        before = h.snapshot()
        with monkeypatch.context() as patch:
            probe = install(
                patch,
                h,
                lambda r: (
                    r.record_type == "CONFLICT"
                    and r.state == "OPEN_CONFLICT"
                    and r.payload.get("aggregate_id") == "PROJECT_HEAD_SET"
                ),
            )
            await rollback_and_replay(h, probe, "revision/changeSet/validate", body, before, "held")
            assert probe.order == [
                "control:CHANGE_SET:HELD",
                "lineage:CHANGE_SET",
                "control:CONFLICT:OPEN_CONFLICT",
            ]
            assert probe.controls[0].record_id == change["record_id"]
            mark = len(probe.controls)
            retried = value(await h.call("revision/changeSet/validate", "retry", body))
            assert retried["state"] == "HELD"
            assert retried["checks"]["optimistic"] is False
            assert retried["checks"]["branch_preservation"] is False
            after = h.snapshot()
            assert_added_controls(before, after, probe.controls[mark:])
            assert len(probe.controls[mark:]) == 2
            assert_new_control_scopes(h, before, after, probe.controls[mark:])
        assert dict(h.runtime.ledger.read_heads(h.project)) == h.heads
        check_reopened(h.runtime.ledger.engine, after)
        save(
            "held-retry",
            before,
            after,
            probe,
            previous_version=change["version"],
            current_version=retried["change_set"]["version"],
        )


async def test_branch_control_and_its_lineage_rollback_together_after_lineage_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with harness(tmp_path) as h:
        body: dict[str, object] = {
            "aggregate_id": h.object_id,
            "from_revision_digest": h.heads[h.key],
            "purpose": "102-V control-lineage",
            "actor_or_agent_ref": "agent:102-v",
        }
        before = h.snapshot()
        with monkeypatch.context() as patch:
            probe = install(
                patch,
                h,
                lambda r: (
                    r.record_type == "BRANCH"
                    and r.state == "OPEN"
                    and r.payload.get("aggregate_id") == h.object_id
                ),
                fault_at="lineage",
            )
            await rollback_and_replay(h, probe, "revision/branch/create", body, before, "lineage")
            assert probe.order == ["control:BRANCH:OPEN", "lineage:BRANCH"]
            assert probe.lineage == [probe.controls[0].record_digest]
            assert (
                SqliteResourceScopeStore(h.runtime.ledger.engine).read(
                    h.project, "control:" + probe.controls[0].record_digest
                )
                is None
            )
            mark = len(probe.controls)
            retried = value(await h.call("revision/branch/create", "retry", body))
            after = h.snapshot()
            assert_added_controls(before, after, probe.controls[mark:])
            assert len(probe.controls[mark:]) == 1
            assert_new_control_scopes(h, before, after, probe.controls[mark:])
            assert retried["branch"]["record_id"] == probe.controls[-1].record_id
            assert retried["content_mutated"] is False
        allowed = {
            "control_records",
            "resource_scope_history",
            "resource_scope_heads",
            "resource_scope_receipts",
        }
        for table in before:
            if table not in allowed:
                assert before[table] == after[table], table
        assert dict(h.runtime.ledger.read_heads(h.project)) == h.heads
        check_reopened(h.runtime.ledger.engine, after)
        save("lineage-retry", before, after, probe, model_calls=h.model.calls)
