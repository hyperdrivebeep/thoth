"""An investigation is saved unless a record it read or wrote changed while it ran."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import BaseModel, JsonValue
from tests.integration.readset_helpers import add_revision
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_memory_edit import propose
from tests.integration.test_research_request_v2 import ControlledResearchModel, setup

from thoth.application.services import FullProjectMemoryService
from thoth.apps.runtime_types import AppRuntime
from thoth.domain.enums import EntityType, ModelRole
from thoth.domain.model import ModelRequest, ModelResult

Json = dict[str, JsonValue]


class GatedModel(ControlledResearchModel):
    """Holds the armed run at its first hypothesis call, which is inside the cycle."""

    def __init__(self) -> None:
        super().__init__(one=True)
        self.armed = False
        self.reached = asyncio.Event()
        self.go = asyncio.Event()

    async def structured[T: BaseModel](self, request: ModelRequest[T]) -> ModelResult[T]:
        if self.armed and request.role == ModelRole.HYPOTHESIS_GENERATOR:
            self.armed = False
            self.reached.set()
            await self.go.wait()
        return await super().structured(request)


async def held_second_run(tmp_path: Path) -> tuple[AppRuntime, GatedModel, Json]:
    """One finished investigation, then a second one held inside its cycle."""

    model = GatedModel()
    runtime = await setup(tmp_path, model)
    first = value(
        await runtime.bus.dispatch(
            request(
                "thread/start",
                "rs-first",
                {
                    "project_id": "p",
                    "problem": "LAB-42의 지연과 조건을 알려줘",
                    "contract_version": 2,
                },
            )
        )
    )
    await runtime.bus.drain()
    done = runtime.bus.read_operation(str(first["operation_id"]))
    assert done is not None and done.state.value == "SUCCEEDED", done
    model.armed = True
    second = value(
        await runtime.bus.dispatch(
            request(
                "thread/input",
                "rs-second",
                {
                    "project_id": "p",
                    "thread_id": first["thread_id"],
                    "contract_version": 2,
                    "instruction": "조건 alpha로 한정",
                },
            )
        )
    )
    await asyncio.wait_for(model.reached.wait(), 60)
    return runtime, model, {**second, "thread_id": first["thread_id"]}


async def finish(runtime: AppRuntime, model: GatedModel, second: Json) -> Json:
    model.go.set()
    await runtime.bus.drain()
    operation = runtime.bus.read_operation(str(second["operation_id"]))
    assert operation is not None
    status = value(
        await runtime.bus.dispatch(
            request(
                "thread/read",
                "rs-read",
                {"project_id": "p", "thread_id": str(second["thread_id"]), "view": "FULL"},
            )
        )
    )
    return {"state": operation.state.value, "error": operation.error, "status": status}


def disposition(outcome: Json) -> object:
    result = cast(Json, cast(Json, cast(Json, outcome["status"])["current_result"])["result"])
    return cast(Json, result["commit"])["disposition"]


@pytest.mark.asyncio
async def test_records_added_meanwhile_that_the_investigation_never_read_do_not_branch_it(
    tmp_path: Path,
) -> None:
    runtime, model, second = await held_second_run(tmp_path)
    try:
        add_revision(runtime, "p", EntityType.EVIDENCE, "unrelated:rs", {"note": "another job"})
        outcome = await finish(runtime, model, second)
        assert outcome["state"] == "SUCCEEDED", outcome["error"]
        assert disposition(outcome) == "FAST_FORWARD"
        assert "EVIDENCE:unrelated:rs" in runtime.ledger.read_heads("p")
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_a_memory_correction_made_meanwhile_neither_branches_nor_fails_the_memory_save(
    tmp_path: Path,
) -> None:
    runtime, model, second = await held_second_run(tmp_path)
    try:
        listed = value(
            await runtime.bus.dispatch(
                request("memory/revision/list", "rs-memories", {"project_id": "p"})
            )
        )
        target = next(
            row
            for row in cast(list[Json], listed["revisions"])
            if row["is_latest"] is True and row["owner_is_current"] is True
        )
        stored_before = len(cast(list[Json], listed["revisions"]))
        edit = await runtime.bus.dispatch(
            request(
                "memory/edit/propose",
                "rs-edit",
                {
                    "project_id": "p",
                    "target_revision_digest": target["revision_digest"],
                    "corrected_text": "지연 측정은 조건 alpha에서 다시 확인한다",
                    "reason": "조사가 도는 동안 사용자가 정정함",
                },
            )
        )
        assert edit.error is None, edit.error
        outcome = await finish(runtime, model, second)
        assert outcome["state"] == "SUCCEEDED", outcome["error"]
        assert disposition(outcome) == "FAST_FORWARD"
        after = value(
            await runtime.bus.dispatch(
                request("memory/revision/list", "rs-memories-2", {"project_id": "p"})
            )
        )
        # the correction and the investigation's own new memories were both stored
        assert len(cast(list[Json], after["revisions"])) > stored_before + 1
        result = cast(Json, cast(Json, cast(Json, outcome["status"])["current_result"])["result"])
        assert cast(Json, result["full_project_memory"])["receipts"]
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_a_record_the_investigation_read_that_changes_meanwhile_still_branches_it(
    tmp_path: Path,
) -> None:
    runtime, model, second = await held_second_run(tmp_path)
    try:
        heads = runtime.ledger.read_heads("p")
        key = next(k for k in heads if k.startswith("ACTION:action:"))
        head = runtime.ledger.read_revision_by_digest("p", heads[key])
        assert head is not None
        snapshot = runtime.ledger.read_snapshot(head.snapshot_id)
        assert snapshot is not None
        content = {**snapshot.content, "created_at": "2030-01-01T00:00:00Z"}
        add_revision(runtime, "p", EntityType.ACTION, key.split(":", 1)[1], content)
        outcome = await finish(runtime, model, second)
        assert "THREAD_CYCLE_HEAD_CONFLICT" in str(outcome), outcome
    finally:
        runtime.close()


async def late_memory_during_preparation(
    runtime: AppRuntime, monkeypatch: pytest.MonkeyPatch, times: int
) -> dict[str, int]:
    """After the memory review of the held investigation, a user correction lands `times` times."""

    listed = value(
        await runtime.bus.dispatch(
            request("memory/revision/list", "rs-late-list", {"project_id": "p"})
        )
    )
    target = next(
        row
        for row in cast(list[Json], listed["revisions"])
        if row["is_latest"] is True and row["owner_is_current"] is True
    )
    state = {"left": times, "added": 0}
    last = {"digest": str(target["revision_digest"])}
    original = FullProjectMemoryService.prepare_thread_results

    async def prepare(self: FullProjectMemoryService, **kwargs: Any) -> Any:
        prepared = await original(self, **kwargs)
        if state["left"] > 0 and not str(kwargs["thread_id"]).startswith("memory-edit:"):
            state["left"] -= 1
            state["added"] += 1
            response = await propose(
                runtime,
                f"rs-late-{state['added']}",
                "p",
                last["digest"],
                f"지연 측정 조건 alpha 확인 절차 {state['added']}번째 정정",
            )
            assert response.error is None, response.error
            last["digest"] = str(cast(Json, value(response)["revision"])["revision_digest"])
        return prepared

    monkeypatch.setattr(FullProjectMemoryService, "prepare_thread_results", prepare)
    return state


@pytest.mark.asyncio
async def test_memory_added_after_the_review_is_reviewed_again_once_and_saved_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, model, second = await held_second_run(tmp_path)
    try:
        state = await late_memory_during_preparation(runtime, monkeypatch, 1)
        outcome = await finish(runtime, model, second)
        assert state["added"] == 1
        assert outcome["state"] == "SUCCEEDED", outcome["error"]
        assert disposition(outcome) == "FAST_FORWARD"
        result = cast(Json, cast(Json, cast(Json, outcome["status"])["current_result"])["result"])
        assert cast(Json, result["full_project_memory"])["preparation_attempts"] == 2
        # the first save was rolled back whole: the cycle's revisions have exactly one receipt
        committed = cast(list[str], cast(Json, result["commit"])["committed_revision_ids"])
        assert committed
        covering = [
            receipt
            for receipt in runtime.ledger.read_receipts("p")
            if set(committed) & set(receipt.subject_refs)
        ]
        assert len(covering) == 1
        assert all(runtime.ledger.read_revision_by_id("p", ref) is not None for ref in committed)
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_memory_added_again_before_the_second_save_fails_the_investigation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, model, second = await held_second_run(tmp_path)
    try:
        state = await late_memory_during_preparation(runtime, monkeypatch, 2)
        heads_before = dict(runtime.ledger.read_heads("p"))
        outcome = await finish(runtime, model, second)
        assert state["added"] == 2
        assert outcome["state"] == "FAILED", outcome
        after = dict(runtime.ledger.read_heads("p"))
        assert {key for key in after if key not in heads_before} <= {
            key for key in after if key.startswith("MEMORY:")
        }
        assert all(after[key] == heads_before[key] for key in heads_before)
    finally:
        runtime.close()
