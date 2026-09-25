"""Prepared-API tests; apply these after product code, not during the seed RED gate."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from tests.integration.test_a02_autonomous_acquisition import prepare_thread, request, value
from tests.integration.test_a06_full_project_memory import (
    _candidate,  # pyright: ignore[reportPrivateUsage]
)
from tests.integration.test_a06_memory_storage_boundary import memory_counts

from thoth.adapters.runtime import SystemClock, UuidIdGenerator
from thoth.adapters.storage import SqliteFullMemoryStore, SqliteMemoryStore
from thoth.application.services.full_project_memory import FullProjectMemoryService
from thoth.domain.memory import MemoryTransition


@pytest.mark.asyncio
async def test_preparation_is_read_only_and_includes_prior_prepared_commit_in_conflicts(
    tmp_path: Path,
) -> None:
    runtime, _connector, project_id = await prepare_thread(tmp_path, allow_connector=True)
    try:
        value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "a06-prepare-seed",
                    {"project_id": project_id, "thread_id": f"thread:{project_id}"},
                )
            )
        )
        owner = next(iter(runtime.ledger.read_heads(project_id).values()))
        candidates = tuple(
            _candidate(
                memory_id=f"memory:a06:prepared:{index}",
                project_id=project_id,
                owner_revision_ref=owner,
                assertion=assertion,
            )
            for index, assertion in enumerate(
                (
                    "A zephyr lesson says the reusable coefficient is positive",
                    "A zephyr lesson says the reusable coefficient is negative",
                )
            )
        )
        full = SqliteFullMemoryStore(runtime.ledger.engine)
        service = FullProjectMemoryService(
            store=full,
            candidates=SqliteMemoryStore(runtime.ledger.engine),
            ledger=runtime.ledger,
            clock=SystemClock(),
            ids=UuidIdGenerator(),
        )
        basis = service.capture_basis(
            project_id=project_id,
            cutoff_at=datetime(2026, 9, 1, tzinfo=UTC),
            scope={},
        )
        before = memory_counts(runtime.ledger.engine)
        prepared = await service.prepare_thread_results(
            basis=basis,
            thread_id="thread:a06:prepared",
            candidates=candidates,
        )
        assert memory_counts(runtime.ledger.engine) == before
        assert prepared.revisions[0].transition == MemoryTransition.COMMIT
        assert prepared.revisions[1].transition == MemoryTransition.HOLD
        result = service.commit_prepared(prepared)
        assert result.committed and result.held
        with pytest.raises(ValueError, match="MEMORY_PREPARATION_STALE"):
            service.commit_prepared(prepared)
        repeated = await service.promote_thread_results(
            project_id=project_id,
            thread_id="thread:a06:prepared",
            cutoff_at=basis.cutoff_at,
            memory_ids=tuple(item.memory_id for item in candidates),
            scope={},
        )
        assert repeated.committed == result.committed
        assert repeated.held == result.held
        assert repeated.receipts == ()
    finally:
        runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", ["candidate", "full_revision"])
async def test_prepared_promotion_rejects_changed_complete_memory_basis(
    tmp_path: Path,
    drift: str,
) -> None:
    runtime, _connector, project_id = await prepare_thread(tmp_path, allow_connector=True)
    try:
        value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "a06-memory-cas-seed",
                    {"project_id": project_id, "thread_id": f"thread:{project_id}"},
                )
            )
        )
        owner = next(iter(runtime.ledger.read_heads(project_id).values()))
        candidate_store = SqliteMemoryStore(runtime.ledger.engine)
        full = SqliteFullMemoryStore(runtime.ledger.engine)
        service = FullProjectMemoryService(
            store=full,
            candidates=candidate_store,
            ledger=runtime.ledger,
            clock=SystemClock(),
            ids=UuidIdGenerator(),
        )
        basis = service.capture_basis(
            project_id=project_id,
            cutoff_at=datetime(2026, 9, 1, tzinfo=UTC),
            scope={},
        )
        prepared = await service.prepare_thread_results(
            basis=basis,
            thread_id="thread:a06:cas",
            candidates=(
                _candidate(
                    memory_id="memory:a06:cas",
                    project_id=project_id,
                    owner_revision_ref=owner,
                    assertion="The independent calibration lesson is reusable",
                ),
            ),
        )
        if drift == "candidate":
            candidate_store.add(
                _candidate(
                    memory_id="memory:a06:concurrent",
                    project_id=project_id,
                    owner_revision_ref=owner,
                    assertion="Concurrent candidate calibration note",
                )
            )
        else:
            full.commit_transition(prepared.revisions[0], prepared.receipts[0])
        before = memory_counts(runtime.ledger.engine)
        with pytest.raises(ValueError, match="MEMORY_PREPARATION_STALE"):
            service.commit_prepared(prepared)
        assert memory_counts(runtime.ledger.engine) == before
    finally:
        runtime.close()
