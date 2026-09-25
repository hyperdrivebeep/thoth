from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from tests.integration.storage_coverage_helpers import (
    domain_snapshot,
    fail_after,
    prepare_project,
    request,
    value,
)
from tests.integration.test_a02_autonomous_acquisition import prepare_thread

from thoth.adapters.runtime import SystemClock
from thoth.adapters.storage.criterion_contract import SqliteCriterionContractStore
from thoth.apps.runtime import AppRuntime, create_runtime
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.criterion_contract import CriterionAuditRecord, CriterionContractRecord


async def prepare(workspace: Path) -> tuple[AppRuntime, str, list[str]]:
    inbox = workspace / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "plan.md").write_text(
        "# Experiment criterion\n\nCompare the reported response with the source.\n",
        encoding="utf-8",
    )
    runtime, project = await prepare_project(workspace)
    value(
        await runtime.bus.dispatch(
            request(
                "project/source/connect",
                "a09-source",
                {
                    "project_id": project,
                    "relative_path": "plan.md",
                    "media_type": "text/markdown",
                    "authority": "OFFICIAL",
                    "cutoff_state": "ELIGIBLE",
                    "security_class": "INTERNAL",
                },
            )
        )
    )
    evidence = value(
        await runtime.bus.dispatch(
            request(
                "evidence/list",
                "a09-evidence",
                {"project_id": project},
            )
        )
    )
    return runtime, project, [item["span_id"] for item in evidence["spans"]]


async def compile_contract(
    runtime: AppRuntime,
    project: str,
    spans: list[str],
    key: str = "a09-compile",
) -> dict[str, Any]:
    return value(
        await runtime.bus.dispatch(
            request(
                "criteria/compile",
                key,
                {"project_id": project, "source_span_ids": spans},
            )
        )
    )


def correction(project: str, current: dict[str, Any], spans: list[str]) -> dict[str, Any]:
    return {
        "project_id": project,
        "criterion_id": current["criterion_id"],
        "expected_revision_digest": current["revision_digest"],
        "field_path": "identity.name",
        "proposed_value": "Revised candidate description",
        "evidence_span_ids": spans,
        "reason": "Correct the candidate description from its cited source",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["add_contract", "append_audit"])
async def test_normal_thread_compile_failure_rolls_back_criterion_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
) -> None:
    runtime, _connector, project = await prepare_thread(tmp_path, allow_connector=True)
    try:
        before = domain_snapshot(runtime.ledger.engine)
        with monkeypatch.context() as patch:
            fail_after(patch, SqliteCriterionContractStore, method)
            response = await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "a09-thread-fault",
                    {"project_id": project, "thread_id": f"thread:{project}"},
                )
            )
        assert response.error is not None
        after = domain_snapshot(runtime.ledger.engine)
        # A failed Thread operation may retain its own diagnostic bookkeeping.
        for table in (
            "criterion_contracts",
            "criterion_audit",
            "semantic_revisions",
            "entity_snapshots",
            "revision_parents",
            "working_heads",
            "receipts",
        ):
            assert after[table] == before[table], table
    finally:
        runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["add_contract", "append_audit"])
async def test_public_compile_failure_has_no_partial_domain_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
) -> None:
    runtime, project, spans = await prepare(tmp_path / "workspace")
    try:
        before = domain_snapshot(runtime.ledger.engine)
        with monkeypatch.context() as patch:
            fail_after(patch, SqliteCriterionContractStore, method)
            response = await runtime.bus.dispatch(
                request(
                    "criteria/compile",
                    "a09-compile-fault",
                    {"project_id": project, "source_span_ids": spans},
                )
            )
        assert response.error is not None
        assert domain_snapshot(runtime.ledger.engine) == before
    finally:
        runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["add_conflict", "add_contract", "append_audit"])
async def test_correction_conflict_revision_audit_share_one_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
) -> None:
    runtime, project, spans = await prepare(tmp_path / "workspace")
    try:
        current = (await compile_contract(runtime, project, spans))["criterion"]
        before = domain_snapshot(runtime.ledger.engine)
        with monkeypatch.context() as patch:
            fail_after(patch, SqliteCriterionContractStore, method)
            response = await runtime.bus.dispatch(
                request(
                    "criteria/field/correct",
                    "a09-correct-fault",
                    correction(project, current, spans),
                )
            )
        assert response.error is not None
        assert domain_snapshot(runtime.ledger.engine) == before
    finally:
        runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["add_reference", "append_audit"])
async def test_reference_and_audit_rollback_together(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
) -> None:
    runtime, project, spans = await prepare(tmp_path / "workspace")
    try:
        current = (await compile_contract(runtime, project, spans))["criterion"]
        before = domain_snapshot(runtime.ledger.engine)
        with monkeypatch.context() as patch:
            fail_after(patch, SqliteCriterionContractStore, method)
            response = await runtime.bus.dispatch(
                request(
                    "criteria/reference/generate",
                    "a09-reference-fault",
                    {
                        "project_id": project,
                        "criterion_id": current["criterion_id"],
                        "expected_revision_digest": current["revision_digest"],
                        "source_scope": spans,
                        "scenarios": ["candidate-only"],
                    },
                )
            )
        assert response.error is not None
        assert domain_snapshot(runtime.ledger.engine) == before
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_stale_revalidate_does_not_leave_branch_or_receipt(tmp_path: Path) -> None:
    runtime, project, spans = await prepare(tmp_path / "workspace")
    try:
        current = (await compile_contract(runtime, project, spans))["criterion"]
        update = correction(project, current, spans)
        value(
            await runtime.bus.dispatch(
                request(
                    "criteria/field/correct",
                    "a09-advance",
                    update,
                )
            )
        )
        before = domain_snapshot(runtime.ledger.engine)
        response = await runtime.bus.dispatch(
            request(
                "criteria/revalidate",
                "a09-stale",
                {
                    "project_id": project,
                    "criterion_id": current["criterion_id"],
                    "revision_digest": current["revision_digest"],
                    "trigger_reason": "revalidate a stale caller snapshot",
                },
            )
        )
        assert response.error is not None
        assert "CRITERION_REVISION_CONFLICT" in response.error.message
        assert domain_snapshot(runtime.ledger.engine) == before
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_revision_rechecked_after_handler_read_before_any_conflict_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, project, spans = await prepare(tmp_path / "workspace")
    try:
        current = (await compile_contract(runtime, project, spans))["criterion"]
        stale = CriterionContractRecord.model_validate(current)
        value(
            await runtime.bus.dispatch(
                request(
                    "criteria/field/correct",
                    "a09-winner",
                    correction(project, current, spans),
                )
            )
        )
        before = domain_snapshot(runtime.ledger.engine)
        original = SqliteCriterionContractStore.read_contract
        calls = 0

        def stale_first_read(
            store: SqliteCriterionContractStore,
            project_id: str,
            criterion_id: str,
            revision_digest: str | None,
        ) -> CriterionContractRecord | None:
            nonlocal calls
            calls += 1
            if calls == 1:
                return stale
            return original(store, project_id, criterion_id, revision_digest)

        with monkeypatch.context() as patch:
            patch.setattr(SqliteCriterionContractStore, "read_contract", stale_first_read)
            response = await runtime.bus.dispatch(
                request(
                    "criteria/field/correct",
                    "a09-loser",
                    correction(project, current, spans),
                )
            )
        assert calls >= 2
        assert response.error is not None
        assert "CRITERION_REVISION_CONFLICT" in response.error.message
        assert domain_snapshot(runtime.ledger.engine) == before
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_duplicate_compile_reopens_without_duplicate_revision(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    runtime, project, spans = await prepare(workspace)
    try:
        first = await compile_contract(runtime, project, spans)
        before = domain_snapshot(runtime.ledger.engine)
        assert await compile_contract(runtime, project, spans) == first
        assert domain_snapshot(runtime.ledger.engine) == before
    finally:
        runtime.close()
    reopened = create_runtime(workspace)
    try:
        assert await compile_contract(reopened, project, spans) == first
        assert domain_snapshot(reopened.ledger.engine) == before
        changed_scope = await reopened.bus.dispatch(
            request(
                "criteria/compile",
                "a09-compile",
                {
                    "project_id": project,
                    "source_span_ids": spans,
                    "goal_requirement_refs": ["different-request"],
                },
            )
        )
        assert changed_scope.error is not None
        assert domain_snapshot(reopened.ledger.engine) == before
        current = first["criterion"]
        head = reopened.ledger.read_heads(project)[f"CRITERION:{current['criterion_id']}"]
        assert head == current["revision_digest"]
        stored = SqliteCriterionContractStore(reopened.ledger.engine).read_contract(
            project,
            current["criterion_id"],
            None,
        )
        assert stored is not None and stored.revision_digest == head
        assert current["profile_refs"] == ["GENERAL_RND"]
        assert current["usage_authorization"] == "NOT_AUTHORIZED"
    finally:
        reopened.close()


@pytest.mark.asyncio
async def test_public_audit_digest_binds_the_persisted_advancing_timestamp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ticks = 0

    def advancing_now(clock: SystemClock) -> datetime:
        nonlocal ticks
        del clock
        ticks += 1
        return datetime(2026, 9, 4, tzinfo=UTC) + timedelta(seconds=ticks)

    monkeypatch.setattr(SystemClock, "now", advancing_now)
    runtime, project, spans = await prepare(tmp_path / "workspace")
    try:
        compiled = (await compile_contract(runtime, project, spans))["criterion"]
        current = value(
            await runtime.bus.dispatch(
                request(
                    "criteria/field/correct",
                    "a09-clock-correct",
                    correction(project, compiled, spans),
                )
            )
        )["criterion"]
        value(
            await runtime.bus.dispatch(
                request(
                    "criteria/reference/generate",
                    "a09-clock-reference",
                    {
                        "project_id": project,
                        "criterion_id": current["criterion_id"],
                        "expected_revision_digest": current["revision_digest"],
                        "source_scope": spans,
                        "scenarios": ["candidate-only"],
                    },
                )
            )
        )
        audit = value(
            await runtime.bus.dispatch(
                request(
                    "criteria/audit/read",
                    "a09-clock-audit",
                    {
                        "project_id": project,
                        "criterion_id": current["criterion_id"],
                    },
                )
            )
        )
        records = [CriterionAuditRecord.model_validate(item) for item in audit["records"]]
        assert {record.event_type for record in records} == {
            "criteria/compiled",
            "criteria/conflictUpdated",
            "criteria/updated",
            "criteria/referenceUpdated",
        }
        assert tuple(records) == SqliteCriterionContractStore(runtime.ledger.engine).list_audit(
            project, current["criterion_id"]
        )
        for record in records:
            persisted = record.model_dump(
                mode="python",
                exclude={"audit_id", "event_digest", "schema_version"},
            )
            assert record.event_digest == domain_digest(
                "CRITERION_AUDIT",
                "1.0.0",
                canonical_payload(persisted),
            )
    finally:
        runtime.close()
    reopened = create_runtime(tmp_path / "workspace")
    try:
        assert tuple(records) == SqliteCriterionContractStore(reopened.ledger.engine).list_audit(
            project, current["criterion_id"]
        )
    finally:
        reopened.close()


async def current_readback(
    runtime: AppRuntime,
    project: str,
    expected: dict[str, Any],
    key: str,
) -> None:
    read = value(
        await runtime.bus.dispatch(
            request(
                "criteria/read",
                f"{key}-read",
                {
                    "project_id": project,
                    "criterion_id": expected["criterion_id"],
                },
            )
        )
    )["criterion"]
    listed = value(
        await runtime.bus.dispatch(
            request(
                "criteria/list",
                f"{key}-list",
                {"project_id": project},
            )
        )
    )["criteria"]
    assert read == expected
    assert listed == [expected]
    assert (
        runtime.ledger.read_heads(project)[f"CRITERION:{expected['criterion_id']}"]
        == expected["revision_digest"]
    )


@pytest.mark.asyncio
async def test_same_timestamp_revisions_remain_current_in_read_list_and_reopen(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fixed_now(clock: SystemClock) -> datetime:
        del clock
        return datetime(2026, 9, 4, tzinfo=UTC)

    monkeypatch.setattr(SystemClock, "now", fixed_now)
    workspace = tmp_path / "workspace"
    runtime, project, spans = await prepare(workspace)
    try:
        initial = (await compile_contract(runtime, project, spans))["criterion"]
        current = initial
        for index, name in enumerate(("Revised candidate", "Latest candidate")):
            inputs = correction(project, current, spans)
            inputs["proposed_value"] = name
            current = value(
                await runtime.bus.dispatch(
                    request(
                        "criteria/field/correct",
                        f"a09-fixed-correct-{index}",
                        inputs,
                    )
                )
            )["criterion"]
            assert current["created_at"] == initial["created_at"]
            assert current["revision_digest"] != initial["revision_digest"]
            await current_readback(runtime, project, current, f"a09-fixed-{index}")
        historical = value(
            await runtime.bus.dispatch(
                request(
                    "criteria/read",
                    "a09-fixed-history",
                    {
                        "project_id": project,
                        "criterion_id": initial["criterion_id"],
                        "revision_digest": initial["revision_digest"],
                    },
                )
            )
        )["criterion"]
        assert historical == initial
    finally:
        runtime.close()
    reopened = create_runtime(workspace)
    try:
        await current_readback(reopened, project, current, "a09-fixed-reopened")
        revisions = reopened.ledger.read_revisions(
            project,
            "CRITERION",
            current["criterion_id"],
        )
        assert len(revisions) == 3
    finally:
        reopened.close()
