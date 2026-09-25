import json
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import func, select
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_research_request_v2 import ControlledResearchModel, setup

from thoth.adapters.storage.schema import operations
from thoth.apps.runtime import create_runtime


@pytest.mark.asyncio
async def test_query_polling_adds_no_operations_and_rejects_mutations(tmp_path: Path):
    runtime = await setup(tmp_path, ControlledResearchModel(), source=False)
    try:
        with runtime.ledger.engine.connect() as connection:
            before = connection.scalar(select(func.count()).select_from(operations))
        for i in range(12):
            value(await runtime.bus.query(request("project/read", str(i), {"project_id": "p"})))
        denied = await runtime.bus.query(
            request(
                "project/create",
                "bad-query",
                {
                    "project_id": "new",
                    "name": "Must not be created",
                    "cutoff_at": "2026-09-01T00:00:00Z",
                },
            )
        )
        assert denied.error is not None
        with runtime.ledger.engine.connect() as connection:
            assert connection.scalar(select(func.count()).select_from(operations)) == before
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_real_process_exit_after_admission_replays_without_second_input(tmp_path: Path):
    code = """import asyncio, json, os, sys
from pathlib import Path
from tests.integration.test_research_request_v2 import setup, ControlledResearchModel
from tests.integration.storage_coverage_helpers import request, value
async def main():
    workspace = Path(sys.argv[1])
    runtime = await setup(workspace, ControlledResearchModel(), source=False)
    admitted = value(await runtime.bus.dispatch(request("thread/start", "crash-key", {
        "project_id": "p", "problem": "Process restart", "contract_version": 2})))
    (workspace / "admission.json").write_text(json.dumps(admitted))
    os._exit(0)
asyncio.run(main())
"""
    subprocess.run(
        [sys.executable, "-c", code, str(tmp_path)],
        check=True,
        timeout=25,
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
    )
    admitted = json.loads((tmp_path / "admission.json").read_text())
    model = ControlledResearchModel()
    runtime = create_runtime(tmp_path, model_resolver=model)
    try:
        replay = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "crash-key",
                    {"project_id": "p", "problem": "Process restart", "contract_version": 2},
                )
            )
        )
        assert replay["input_id"] == admitted["input_id"]
        assert replay["thread_id"] == admitted["thread_id"]
        await runtime.bus.drain()
        operation = runtime.bus.read_operation(admitted["operation_id"])
        assert operation is not None and operation.state.value == "SUCCEEDED"
        assert model.calls
        state = value(
            await runtime.bus.query(
                request(
                    "thread/read", "check", {"project_id": "p", "thread_id": admitted["thread_id"]}
                )
            )
        )
        assert len(state["inputs"]) == 1
        assert state["budget"]["calls"] == len(model.calls)
    finally:
        runtime.close()
