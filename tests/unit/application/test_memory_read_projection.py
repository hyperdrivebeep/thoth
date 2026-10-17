"""Characterize policy selection and projection status before moving pure calculations."""

from datetime import UTC, datetime
from typing import cast

import pytest
from pydantic import JsonValue
from tests.unit.application.test_action_read_projection import NoCalls

from thoth.application.commands import memory_full
from thoth.application.services.control_record_service import ControlRecordService
from thoth.domain.control_record import ControlRecord
from thoth.domain.memory import MemoryProjection
from thoth.ports.control_record import ControlRecordStorePort
from thoth.ports.ledger import LedgerPort
from thoth.ports.memory import FullMemoryStorePort, MemoryStorePort
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode


class Sources(NoCalls):
    def __init__(self, count: int, fail: str | None = None) -> None:
        self.fail = fail
        self.calls: list[tuple[str, ...]] = []
        now = datetime(2026, 10, 12, tzinfo=UTC)
        self.tasks = tuple(
            ControlRecord(
                control_revision_id=f"r:{i}",
                project_id="p",
                namespace="MEMORY",
                record_type="PROJECTION_TASK",
                record_id=f"task:{i}",
                version=1,
                state="STALE" if i == count - 1 else "RUNNING",
                payload={"checkpoint": "cp:1"} if i == 0 else {},
                record_digest="a" * 64,
                created_at=now,
            )
            for i in range(count)
        )
        self.projections = tuple(
            MemoryProjection(
                projection_id=f"projection:{i}",
                project_id="p",
                projection_type=kind,
                source_revision_digests=(),
                payload_digest="b" * 64,
                rebuild_checkpoint="c" * 64,
                created_at=now,
            )
            for i, kind in enumerate(("SUMMARY", "KEYWORD", "SUMMARY"))
        )

    def list(self, project: str, namespace: str, kind: str) -> tuple[ControlRecord, ...]:
        self.calls.append(("list", project, namespace, kind))
        if self.fail == "tasks":
            raise RuntimeError("tasks unavailable")
        return self.tasks

    def list_projections(self, project: str) -> tuple[MemoryProjection, ...]:
        self.calls.append(("projections", project))
        if self.fail == "projections":
            raise RuntimeError("projections unavailable")
        return self.projections


def handler(sources: Sources | None = None) -> memory_full.MemoryFullHandlers:
    source = sources if sources is not None else NoCalls()
    return memory_full.MemoryFullHandlers(
        records=cast(ControlRecordStorePort, source),
        controls=cast(ControlRecordService, NoCalls()),
        legacy=cast(MemoryStorePort, NoCalls()),
        full=cast(FullMemoryStorePort, source),
        ledger=cast(LedgerPort, NoCalls()),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [None, "DOMAIN_REFERENCE", "MEMORY_ASSERTION", "", "unknown"])
@pytest.mark.parametrize("recall", [None, "FACT", "LESSON", "", "unknown"])
@pytest.mark.parametrize("enabled", [True, False])
async def test_policy_filter_combinations_keep_order_and_original_dicts(
    mode: str | None, recall: str | None, enabled: bool
) -> None:
    value = await handler().policy_list(
        {"project_id": "p", "payload_mode": mode, "recall_class": recall, "enabled_only": enabled}
    )
    indexes: list[int] = []
    if mode in (None, "DOMAIN_REFERENCE") and recall in (None, "FACT"):
        indexes.append(0)
    if mode in (None, "MEMORY_ASSERTION") and recall in (None, "LESSON"):
        indexes.append(1)
    rows = cast(list[dict[str, object]], value["policies"])
    assert rows == [memory_full.POLICIES[i] for i in indexes]
    assert all(row is memory_full.POLICIES[i] for row, i in zip(rows, indexes, strict=True))
    assert list(value) == ["policies"]


@pytest.mark.asyncio
async def test_enabled_is_true_not_truthiness_and_facade_owns_the_policy_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policies = tuple(
        {**memory_full.POLICIES[0], "enabled": enabled, "profile_ref": f"fixture:{i}"}
        for i, enabled in enumerate((False, 1, True))
    )
    monkeypatch.setattr(memory_full, "POLICIES", policies)
    selected = await handler().policy_list({"project_id": "p"})
    assert selected["policies"] == [policies[2]]
    all_policies = await handler().policy_list({"project_id": "p", "enabled_only": False})
    assert all_policies["policies"] == list(policies)


@pytest.mark.asyncio
async def test_policy_lookup_keeps_first_match_and_legacy_version_behavior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = memory_full.POLICIES[0]
    second = {**first, "version": 2}
    monkeypatch.setattr(memory_full, "POLICIES", (first, second))
    value = await handler().policy_read(
        {"project_id": "p", "profile_ref": str(first["profile_ref"]), "version": 99}
    )
    assert value["policy"] is first
    with pytest.raises(RpcApplicationError) as caught:
        await handler().policy_read({"project_id": "p", "profile_ref": "absent"})
    assert (
        caught.value.code == RpcErrorCode.DOMAIN_REJECTED
        and caught.value.message == "policy not found"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [0, 1, 3])
async def test_projection_status_keeps_last_task_checkpoints_constants_and_read_order(
    count: int,
) -> None:
    source = Sources(count)
    value = await handler(source).projection_status({"project_id": "p"})
    expected: dict[str, JsonValue] = {
        "canonical_reference_health": "PASS",
        "index_freshness": "CURRENT" if count == 0 else "STALE",
        "vector_freshness": "DERIVED_ONLY",
        "summary_freshness": "DERIVED_ONLY",
        "rebuild_checkpoints": [] if not count else ["cp:1", *([None] * (count - 1))],
        "leakage_checks": "PROJECT_FILTER_BEFORE_RERANK",
        "full_projection_types": ["SUMMARY", "KEYWORD", "SUMMARY"],
        "derived_projection_canonical_truth": False,
    }
    assert value == expected and list(value) == list(expected)
    assert source.calls == [("list", "p", "MEMORY", "PROJECTION_TASK"), ("projections", "p")]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["tasks", "projections"])
async def test_projection_read_failure_keeps_call_order_and_propagates(failure: str) -> None:
    source = Sources(1, fail=failure)
    with pytest.raises(RuntimeError, match=failure + " unavailable"):
        await handler(source).projection_status({"project_id": "p"})
    expected: list[tuple[str, ...]] = [("list", "p", "MEMORY", "PROJECTION_TASK")]
    if failure == "projections":
        expected.append(("projections", "p"))
    assert source.calls == expected
