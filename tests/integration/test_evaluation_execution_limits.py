"""Real process cleanup and bounded producer failure states."""

import asyncio
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from tests.integration.paired_evaluation_helpers import pair_harness

from thoth.adapters.storage.evaluation_run import SqliteEvaluationRunStore

CASES: list[dict[str, object]] = [
    {
        "public": {"case_id": "one", "payload": {}},
        "expected_output": {"answer": 1},
        "axis": "quality",
    }
]


async def test_timeout_kills_actual_process_and_retains_unscored_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = asyncio.create_subprocess_exec
    processes: list[asyncio.subprocess.Process] = []

    async def slow_process(*_args: Any, **kwargs: Any):
        process = await original(sys.executable, "-c", "import time; time.sleep(20)", **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", slow_process)
    async with pair_harness(
        tmp_path,
        {"op": "literal", "value": 0},
        {"op": "literal", "value": 1},
        CASES,
        timeout_seconds=1,
    ) as h:
        response = await h.run()
        assert response["state"] == "HELD", response
        record = response["pair"]
        assert record["baseline"]["state"] == "TIMEOUT"
        assert record["candidate"] is None and record["result"] is None
        assert record["baseline"]["final_memory_digest"] is None
        # The pair deadline includes preparation before this arm starts.
        receipt = record["baseline"]
        assert receipt["elapsed_ns"] > 0
        assert datetime.fromisoformat(receipt["completed_at"]) >= datetime.fromisoformat(
            record["spec"]["deadline_at"]
        )
    assert len(processes) == 1 and processes[0].returncode is not None


@pytest.mark.parametrize("repeat_cancel", [False, True])
async def test_cancel_during_process_creation_cleans_up_before_return(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, repeat_cancel: bool
) -> None:
    original = asyncio.create_subprocess_exec
    started = asyncio.Event()
    processes: list[asyncio.subprocess.Process] = []

    async def delayed_creation(*_args: Any, **kwargs: Any):
        process = await original(sys.executable, "-c", "import time; time.sleep(20)", **kwargs)
        processes.append(process)
        started.set()
        await asyncio.sleep(0.3)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", delayed_creation)
    async with pair_harness(
        tmp_path, {"op": "literal", "value": 0}, {"op": "literal", "value": 1}, CASES
    ) as h:
        task = asyncio.create_task(h.run())
        await asyncio.wait_for(started.wait(), 5)
        task.cancel()
        if repeat_cancel:
            await asyncio.sleep(0.05)
            task.cancel()
        cleaned = False
        try:
            with pytest.raises(asyncio.CancelledError):
                await task
            cleaned = all(process.returncode is not None for process in processes)
            record = SqliteEvaluationRunStore(h.runtime.ledger.engine).read_plan(
                h.project, h.plan["record_id"], h.plan["record_digest"]
            )
            assert record is not None and record.state == "CANCELLED"
            assert record.result is None
        finally:
            # RED cleanup: the test never leaves the deliberately delayed child alive.
            for process in processes:
                if process.returncode is None:
                    process.kill()
                    await process.wait()
        assert cleaned, "cancellation returned while the evaluation child remained alive"
