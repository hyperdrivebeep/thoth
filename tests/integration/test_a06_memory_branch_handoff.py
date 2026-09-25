"""Seed-compatible normal-entry branch and concurrent-memory regression tests."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sqlalchemy import func, select
from tests.integration.test_a02_autonomous_acquisition import prepare_thread, request, value
from tests.integration.test_a04_r2_closed_loop import prepare_a04
from tests.integration.test_a06_memory_storage_boundary import memory_counts

from thoth.adapters.memory import DeterministicRoleMemoryReviewer
from thoth.adapters.storage import SqliteFullMemoryStore
from thoth.adapters.storage.schema import semantic_revisions
from thoth.application.services.acquisition_coordinator import (
    AcquisitionCoordinator,
    AutonomousAcquisitionExecution,
)
from thoth.application.services.critical_counter_search_coordinator import (
    CriticalCounterSearchCoordinator,
    CriticalCounterSearchExecution,
)
from thoth.application.services.revision_service import CommitDisposition, CommitResult
from thoth.application.workflows.thread_cycle import (
    ThreadCycleCommand,
    ThreadCycleResult,
    ThreadCycleService,
)
from thoth.domain.evidence import EvidenceSpan, InformationSufficiencyAssessment
from thoth.domain.hypothesis import HypothesisPortfolio
from thoth.domain.memory import MemoryReviewContext, MemoryRoleReview
from thoth.domain.project import Project, WorkThread
from thoth.protocol.jsonrpc import JsonRpcResponse


def _observe_downstream(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[str]]:
    calls: dict[str, list[str]] = {"acquisition": [], "critical": []}
    acquire = AcquisitionCoordinator.execute_if_required
    counter = CriticalCounterSearchCoordinator.execute_if_required

    async def acquisition(
        self: AcquisitionCoordinator,
        *,
        project: Project,
        thread: WorkThread,
        assessment: InformationSufficiencyAssessment,
        evidence: tuple[EvidenceSpan, ...],
    ) -> AutonomousAcquisitionExecution | None:
        calls["acquisition"].append(thread.thread_id)
        return await acquire(
            self,
            project=project,
            thread=thread,
            assessment=assessment,
            evidence=evidence,
        )

    async def critical(
        self: CriticalCounterSearchCoordinator,
        *,
        project: Project,
        thread: WorkThread,
        portfolio: HypothesisPortfolio,
    ) -> CriticalCounterSearchExecution | None:
        calls["critical"].append(thread.thread_id)
        return await counter(self, project=project, thread=thread, portfolio=portfolio)

    monkeypatch.setattr(AcquisitionCoordinator, "execute_if_required", acquisition)
    monkeypatch.setattr(CriticalCounterSearchCoordinator, "execute_if_required", critical)
    return calls


@pytest.mark.asyncio
async def test_normal_r2_head_conflict_returns_branch_before_any_downstream_admission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, sandbox, project_id, thread_id = await prepare_a04(tmp_path, allow_sandbox=True)
    calls = _observe_downstream(monkeypatch)
    review = DeterministicRoleMemoryReviewer.review
    changed = False

    async def concurrent_head(
        self: DeterministicRoleMemoryReviewer,
        context: MemoryReviewContext,
    ) -> MemoryRoleReview:
        nonlocal changed
        result = await review(self, context)
        if not changed:
            changed = True
            digest = next(iter(runtime.ledger.read_heads(project_id).values()))
            with runtime.ledger.transaction() as transaction:
                transaction.set_head(project_id, "OBJECT:a06-concurrent", digest)
        return result

    monkeypatch.setattr(DeterministicRoleMemoryReviewer, "review", concurrent_head)
    try:
        result = value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "a06-r2-branch",
                    {"project_id": project_id, "thread_id": thread_id},
                )
            )
        )
        assert result["terminal_state"] == "BRANCHED"
        assert result["reason_code"] == "THREAD_CYCLE_HEAD_CONFLICT"
        assert result["downstream_after_branch_executed"] is False
        branch = CommitResult.model_validate(result["commit"])
        assert branch.disposition == CommitDisposition.BRANCH
        assert branch.branch_revision_ids and not branch.committed_revision_ids
        assert branch.receipt in runtime.ledger.read_receipts(project_id)
        assert calls == {"acquisition": [], "critical": []}
        assert sandbox.seen_specs == []
        assert memory_counts(runtime.ledger.engine) == (0, 0, 0, 0)
        with runtime.ledger.engine.connect() as connection:
            outcomes = connection.execute(
                select(func.count())
                .select_from(semantic_revisions)
                .where(
                    semantic_revisions.c.project_id == project_id,
                    semantic_revisions.c.entity_type == "OUTCOME",
                )
            ).scalar_one()
        assert outcomes == 0
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_concurrent_normal_thread_commit_preserves_loser_branch_and_winner_memory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, sandbox, project_id, first_thread = await prepare_a04(tmp_path, allow_sandbox=True)
    second_thread = "thread:a06:concurrent-winner"
    value(
        await runtime.bus.dispatch(
            request(
                "thread/start",
                "a06-concurrent-second-start",
                {
                    "project_id": project_id,
                    "thread_id": second_thread,
                    "problem": "Run an independent bounded R2 discrimination action.",
                    "scope": {"workstream": "r2-closed-loop"},
                },
            )
        )
    )
    calls = _observe_downstream(monkeypatch)
    paused, release = asyncio.Event(), asyncio.Event()
    review = DeterministicRoleMemoryReviewer.review
    first_task: asyncio.Task[JsonRpcResponse] | None = None

    async def pause_first(
        self: DeterministicRoleMemoryReviewer,
        context: MemoryReviewContext,
    ) -> MemoryRoleReview:
        result = await review(self, context)
        if asyncio.current_task() is first_task and not paused.is_set():
            paused.set()
            await asyncio.wait_for(release.wait(), timeout=20)
        return result

    monkeypatch.setattr(DeterministicRoleMemoryReviewer, "review", pause_first)
    try:
        first_task = asyncio.create_task(
            runtime.bus.dispatch(
                request(
                    "thread/input",
                    "a06-concurrent-loser-input",
                    {"project_id": project_id, "thread_id": first_thread},
                )
            )
        )
        await asyncio.wait_for(paused.wait(), timeout=10)
        winner = value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "a06-concurrent-winner-input",
                    {"project_id": project_id, "thread_id": second_thread},
                )
            )
        )
        assert winner["full_project_memory"]
        assert len(sandbox.seen_specs) == 1
        winner_heads = dict(runtime.ledger.read_heads(project_id))
        winnermemory_counts = memory_counts(runtime.ledger.engine)
        winner_memory = SqliteFullMemoryStore(runtime.ledger.engine).list_revisions(project_id)
        winner_calls = {key: tuple(items) for key, items in calls.items()}
        assert winner_memory
        assert all(item.origin_thread_id == second_thread for item in winner_memory)

        release.set()
        loser = value(await asyncio.wait_for(first_task, timeout=10))
        branch = CommitResult.model_validate(loser["commit"])
        assert loser["terminal_state"] == "BRANCHED"
        assert loser["downstream_after_branch_executed"] is False
        assert branch.disposition == CommitDisposition.BRANCH and branch.branch_revision_ids
        assert branch.receipt in runtime.ledger.read_receipts(project_id)
        with runtime.ledger.engine.connect() as connection:
            persisted = (
                connection.execute(
                    select(semantic_revisions.c.revision_id).where(
                        semantic_revisions.c.revision_id.in_(branch.branch_revision_ids),
                    )
                )
                .scalars()
                .all()
            )
        assert set(persisted) == set(branch.branch_revision_ids)
        assert dict(runtime.ledger.read_heads(project_id)) == winner_heads
        assert memory_counts(runtime.ledger.engine) == winnermemory_counts
        after_memory = SqliteFullMemoryStore(runtime.ledger.engine).list_revisions(project_id)
        assert after_memory == winner_memory
        assert {key: tuple(items) for key, items in calls.items()} == winner_calls
        assert len(sandbox.seen_specs) == 1
    finally:
        release.set()
        if first_task is not None and not first_task.done():
            first_task.cancel()
            await asyncio.gather(first_task, return_exceptions=True)
        runtime.close()


@pytest.mark.asyncio
async def test_reanalysis_branch_stops_before_critical_and_r2(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, _connector, project_id = await prepare_thread(tmp_path, allow_connector=True)
    calls = _observe_downstream(monkeypatch)
    execute = ThreadCycleService.execute
    review = DeterministicRoleMemoryReviewer.review
    cycles = 0
    changed = False
    memory_before_second: tuple[int, ...] | None = None

    async def count_cycle(
        self: ThreadCycleService,
        command: ThreadCycleCommand,
    ) -> ThreadCycleResult:
        nonlocal cycles, memory_before_second
        cycles += 1
        if cycles == 2:
            memory_before_second = memory_counts(runtime.ledger.engine)
        return await execute(self, command)

    async def conflict_second(
        self: DeterministicRoleMemoryReviewer,
        context: MemoryReviewContext,
    ) -> MemoryRoleReview:
        nonlocal changed
        result = await review(self, context)
        if cycles == 2 and not changed:
            changed = True
            digest = next(iter(runtime.ledger.read_heads(project_id).values()))
            with runtime.ledger.transaction() as transaction:
                transaction.set_head(project_id, "OBJECT:a06-second-cycle-conflict", digest)
        return result

    monkeypatch.setattr(ThreadCycleService, "execute", count_cycle)
    monkeypatch.setattr(DeterministicRoleMemoryReviewer, "review", conflict_second)
    try:
        result = value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "a06-reanalysis-branch",
                    {"project_id": project_id, "thread_id": f"thread:{project_id}"},
                )
            )
        )
        assert cycles == 2 and changed
        assert result["prior_acquisition_completed"] is True
        assert result["terminal_state"] == "BRANCHED"
        assert result["downstream_after_branch_executed"] is False
        assert len(calls["acquisition"]) == 1 and calls["critical"] == []
        assert memory_counts(runtime.ledger.engine) == memory_before_second
        branch = CommitResult.model_validate(result["commit"])
        assert branch.receipt in runtime.ledger.read_receipts(project_id)
    finally:
        runtime.close()
