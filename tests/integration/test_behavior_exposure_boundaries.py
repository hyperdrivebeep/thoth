import asyncio
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel
from tests.integration.behavior_exposure_helpers import exposure_harness
from tests.integration.storage_coverage_helpers import value

from thoth.adapters.runtime import SystemClock
from thoth.adapters.storage.behavior_execution import SqliteBehaviorExecutionStore
from thoth.adapters.storage.control_record import SqliteControlRecordStore
from thoth.domain.model import ModelRequest


async def test_request_cap_finishes_trial_and_next_work_uses_baseline(tmp_path: Path) -> None:
    async with exposure_harness(tmp_path, requests=1) as h:
        await h.arm()
        value(await h.call("thread/input", "one-trial", {"thread_id": h.thread}))
        current = value(
            await h.call(
                "improvement/exposure/runtime/read",
                "cap-read",
                {
                    "exposure_id": h.exposure["spec"]["exposure_id"],
                },
            )
        )["exposure"]
        assert current["state"] == "COMPLETED" and current["request_count"] == 1
        h.model.guidance.clear()
        value(await h.call("thread/input", "after-cap", {"thread_id": h.thread}))
        assert set(h.model.guidance) == {"baseline-guidance"}


async def test_expiry_during_model_call_holds_output_and_withdraws_canary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with exposure_harness(tmp_path) as h:
        await h.arm()
        original_time = SystemClock.now
        original_model = h.model.structured
        advanced = False

        def now(clock: SystemClock):
            return original_time(clock) + timedelta(seconds=600 if advanced else 0)

        async def delayed_expiry(request: ModelRequest[BaseModel]):
            nonlocal advanced
            result = await original_model(request)
            advanced = True
            return result

        monkeypatch.setattr(SystemClock, "now", now)
        monkeypatch.setattr(h.model, "structured", delayed_expiry)
        failed = await h.call("thread/input", "expires-in-flight", {"thread_id": h.thread})
        assert failed.error is not None and failed.error.data is not None
        assert failed.error.data["reason_code"] == "BEHAVIOR_EXPOSURE_EXPIRED"
        current = value(
            await h.call(
                "improvement/exposure/runtime/read",
                "expired-read",
                {
                    "exposure_id": h.exposure["spec"]["exposure_id"],
                },
            )
        )["exposure"]
        assert (
            current["state"] == "ROLLED_BACK" and not current["observations"][0]["decision_exposed"]
        )


async def test_inflight_work_keeps_snapshot_when_baseline_is_promoted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with exposure_harness(tmp_path) as h:
        await h.arm()
        value(await h.call("thread/input", "trial", {"thread_id": h.thread}))
        await h.finish_trial()
        promotion = await h.prepare_baseline()
        h.model.guidance.clear()
        entered, release = asyncio.Event(), asyncio.Event()
        original = h.model.structured

        async def paused(request: ModelRequest[BaseModel]):
            if not entered.is_set():
                entered.set()
                await asyncio.wait_for(release.wait(), 10)
            return await original(request)

        monkeypatch.setattr(h.model, "structured", paused)
        running = asyncio.create_task(
            h.call("thread/input", "old-snapshot", {"thread_id": h.thread})
        )
        try:
            await asyncio.wait_for(entered.wait(), 10)
            value(await h.approve_baseline(promotion))
        finally:
            release.set()
        completed = value(await running)
        assert set(h.model.guidance) == {"baseline-guidance"}
        assert all(
            item["content_digest"] != h.exposure["spec"]["candidate_digest"]
            for item in completed["behavior_execution"]["snapshots"]
        )
        h.model.guidance.clear()
        value(await h.call("thread/input", "new-snapshot", {"thread_id": h.thread}))
        assert set(h.model.guidance) == {"candidate-guidance"}


async def test_promotion_fault_preserves_pending_approval_and_previous_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with exposure_harness(tmp_path) as h:
        await h.arm()
        value(await h.call("thread/input", "trial", {"thread_id": h.thread}))
        await h.finish_trial()
        promotion = await h.prepare_baseline()
        original = SqliteBehaviorExecutionStore.promote

        def fail_after_write(
            store: SqliteBehaviorExecutionStore, *args: Any, **kwargs: Any
        ) -> None:
            original(store, *args, **kwargs)
            raise RuntimeError("injected after baseline history write")

        with monkeypatch.context() as patch:
            patch.setattr(SqliteBehaviorExecutionStore, "promote", fail_after_write)
            failed = await h.approve_baseline(promotion, "failed-promotion")
            assert failed.error is not None
        current = SqliteControlRecordStore(h.pair.runtime.ledger.engine).read(
            h.pair.project, "IMPROVEMENT", promotion["record_id"]
        )
        assert (
            current is not None
            and current.state == "PENDING"
            and current.record_digest == promotion["record_digest"]
        )
        assert (
            SqliteBehaviorExecutionStore(h.pair.runtime.ledger.engine).baselines(
                h.pair.project, h.pair.binding.component, "LOCAL"
            )
            == ()
        )
        value(await h.approve_baseline(promotion, "retry-promotion"))
