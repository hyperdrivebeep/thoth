"""Memory preparation compares what an investigation read, against the memory of the moment."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from tests.integration.readset_helpers import add_revision
from tests.integration.test_a02_autonomous_acquisition import prepare_thread, request, value
from tests.integration.test_a06_full_project_memory import (
    _candidate,  # pyright: ignore[reportPrivateUsage]
)

from thoth.adapters.runtime import SystemClock, UuidIdGenerator
from thoth.adapters.storage import SqliteFullMemoryStore, SqliteMemoryStore
from thoth.application.services import FullProjectMemoryService
from thoth.domain.enums import EntityType, MemoryKind
from thoth.domain.memory_preparation import MemoryPreparationHeadChanged

CUTOFF = datetime(2026, 9, 1, tzinfo=UTC)
SCOPE = {"workstream": "dataset-audit"}


async def build(tmp_path: Path):  # type: ignore[no-untyped-def]
    runtime, _connector, project = await prepare_thread(tmp_path, allow_connector=True)
    first = value(
        await runtime.bus.dispatch(
            request(
                "thread/input", "rs-seed", {"project_id": project, "thread_id": f"thread:{project}"}
            )
        )
    )
    owner = str(first["full_project_memory"]["committed"][0]["owner_revision_ref"])  # type: ignore[index]
    legacy = SqliteMemoryStore(runtime.ledger.engine)
    service = FullProjectMemoryService(
        store=SqliteFullMemoryStore(runtime.ledger.engine),
        candidates=legacy,
        ledger=runtime.ledger,
        clock=SystemClock(),
        ids=UuidIdGenerator(),
    )
    return runtime, project, owner, legacy, service


def lesson(project: str, owner: str, name: str, text: str):  # type: ignore[no-untyped-def]
    return _candidate(
        memory_id=f"memory:rs:{name}", project_id=project, owner_revision_ref=owner, assertion=text
    )


async def add_memory(service, legacy, project, owner, name, text):  # type: ignore[no-untyped-def]
    record = lesson(project, owner, name, text)
    legacy.add(record)
    result = await service.promote_thread_results(
        project_id=project,
        thread_id=f"thread:rs:{name}",
        cutoff_at=CUTOFF,
        memory_ids=(record.memory_id,),
        scope=SCOPE,
    )
    assert result.committed
    return result


PARENT = "1" * 64


async def add_correction(runtime, service, legacy, project, owner, name, text):  # type: ignore[no-untyped-def]
    """A memory that corrects the stored version PARENT, committed on its own."""
    record = lesson(project, owner, name, text)
    legacy.add(record)
    prepared = await service.prepare_thread_results(
        basis=service.capture_basis(project_id=project, cutoff_at=CUTOFF, scope=SCOPE),
        thread_id=f"thread:rs:{name}",
        candidates=(record,),
        parent_by_memory_id={record.memory_id: PARENT},
    )
    with runtime.ledger.transaction():
        service.commit_prepared(prepared)
    return record


@pytest.mark.asyncio
async def test_a_head_no_one_read_does_not_stop_preparation_but_a_head_that_was_read_does(
    tmp_path: Path,
) -> None:
    runtime, project, owner, legacy, service = await build(tmp_path)
    try:
        heads = dict(runtime.ledger.read_heads(project))
        read_key = next(key for key in heads if key.startswith("OUTCOME:"))
        basis = service.capture_basis(project_id=project, cutoff_at=CUTOFF, scope=SCOPE)
        scoped = service.rebase_basis(basis, head_scope={read_key: heads[read_key]}, head_absent=())
        add_revision(runtime, project, EntityType.EVIDENCE, "unrelated:rs", {"note": "other job"})
        candidate = lesson(project, owner, "head", "Reuse dataset version notes during audits")
        legacy.add(candidate)
        # the whole-head comparison the old code made
        with pytest.raises(MemoryPreparationHeadChanged):
            await service.prepare_thread_results(
                basis=basis, thread_id="t", candidates=(candidate,)
            )
        # narrowed to what was read: an unrelated new head is fine
        prepared = await service.prepare_thread_results(
            basis=service.rebase_basis(
                scoped, head_scope=dict(scoped.head_scope or ()), head_absent=()
            ),
            thread_id="t",
            candidates=(candidate,),
        )
        assert prepared.result.committed
        # a head that was read and then changed still stops it
        add_revision(
            runtime, project, EntityType.OUTCOME, read_key.split(":", 1)[1], {"changed": 1}
        )
        with pytest.raises(MemoryPreparationHeadChanged):
            await service.prepare_thread_results(
                basis=scoped, thread_id="t", candidates=(candidate,)
            )
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_a_head_that_did_not_exist_and_appears_stops_preparation(tmp_path: Path) -> None:
    runtime, project, owner, legacy, service = await build(tmp_path)
    try:
        basis = service.capture_basis(project_id=project, cutoff_at=CUTOFF, scope=SCOPE)
        scoped = service.rebase_basis(basis, head_scope={}, head_absent=("EVIDENCE:new:rs",))
        candidate = lesson(project, owner, "absent", "Reuse dataset version notes during audits")
        legacy.add(candidate)
        add_revision(
            runtime, project, EntityType.EVIDENCE, "new:rs", {"note": "someone else first"}
        )
        with pytest.raises(MemoryPreparationHeadChanged):
            await service.prepare_thread_results(
                basis=scoped, thread_id="t", candidates=(candidate,)
            )
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_memory_added_meanwhile_is_judged_against_at_preparation_and_never_from_the_old_list(
    tmp_path: Path,
) -> None:
    runtime, project, owner, legacy, service = await build(tmp_path)
    try:
        basis = service.capture_basis(project_id=project, cutoff_at=CUTOFF, scope=SCOPE)
        await add_correction(
            runtime, service, legacy, project, owner, "first", "dataset version lessons are kept"
        )
        candidate = lesson(project, owner, "second", "dataset version lessons can be skipped")
        legacy.add(candidate)
        # the list captured at the start is stale: it is refused, not used
        with pytest.raises(ValueError, match="MEMORY_PREPARATION_STALE"):
            await service.prepare_thread_results(
                basis=basis, thread_id="t", candidates=(candidate,)
            )
        fresh = service.rebase_basis(basis, head_scope={}, head_absent=())
        # Another correction of the same starting version was added meanwhile: which one stands
        # is not decidable, so the newer one is held against it.
        prepared = await service.prepare_thread_results(
            basis=fresh,
            thread_id="t",
            candidates=(candidate,),
            parent_by_memory_id={candidate.memory_id: PARENT},
        )
        (held,) = prepared.result.held
        assert held.kind == MemoryKind.LESSON and held.memory_id == candidate.memory_id
        assert [r.reason_code for r in held.reviews if r.verdict.value == "HOLD"]
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_memory_added_between_preparation_and_commit_still_fails_instead_of_saving_stale(
    tmp_path: Path,
) -> None:
    runtime, project, owner, legacy, service = await build(tmp_path)
    try:
        candidate = lesson(project, owner, "late", "Reuse dataset version notes during audits")
        legacy.add(candidate)
        basis = service.rebase_basis(
            service.capture_basis(project_id=project, cutoff_at=CUTOFF, scope=SCOPE),
            head_scope={},
            head_absent=(),
        )
        prepared = await service.prepare_thread_results(
            basis=basis, thread_id="t", candidates=(candidate,)
        )
        await add_memory(
            service, legacy, project, owner, "meanwhile", "unrelated practice notes exist"
        )
        with (
            pytest.raises(ValueError, match="MEMORY_PREPARATION_STALE"),
            runtime.ledger.transaction(),
        ):
            service.commit_prepared(prepared)
    finally:
        runtime.close()
