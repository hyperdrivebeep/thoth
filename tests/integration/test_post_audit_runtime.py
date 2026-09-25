"""Desired behavior replacing the audit's bug-observation assertions."""

from pathlib import Path
from typing import Any

import pytest
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_research_request_v2 import ControlledResearchModel, setup

from thoth.application.services.request_records import RequestRecords
from thoth.domain.research_request import CurrentResultManifest
from thoth.protocol.bus import DispatchTicket


@pytest.mark.asyncio
async def test_duplicate_claim_tickets_replay_without_poisoning_the_operation(tmp_path: Path):
    runtime = await setup(tmp_path, ControlledResearchModel(), source=False)
    try:
        req = request(
            "thread/start",
            "same-key",
            {"project_id": "p", "problem": "One request", "contract_version": 2},
        )
        first, second = runtime.bus.claim(req), runtime.bus.claim(req)
        assert isinstance(first, DispatchTicket) and isinstance(second, DispatchTicket)
        a = value(await runtime.bus.execute(first))
        b = value(await runtime.bus.execute(second))
        assert a["input_id"] == b["input_id"]
        await runtime.bus.drain()
        operation = runtime.bus.read_operation(str(a["operation_id"]))
        assert operation is not None and operation.state.value == "SUCCEEDED", operation
        threads = value(
            await runtime.bus.dispatch(request("thread/list", "threads", {"project_id": "p"}))
        )
        assert len(threads["threads"]) == 1
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_requirement_checkpoint_failure_rolls_back_its_records_and_heads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    runtime = await setup(tmp_path, ControlledResearchModel(), source=False)
    original = RequestRecords.save

    def fail(self: RequestRecords, *args: Any, **kwargs: Any):
        record = args[3]
        if isinstance(record, CurrentResultManifest) and record.phase == "REQUIREMENTS":
            raise RuntimeError("INJECTED_CHECKPOINT_FAILURE")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(RequestRecords, "save", fail)
    try:
        accepted = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "start",
                    {"project_id": "p", "problem": "Atomic checkpoint", "contract_version": 2},
                )
            )
        )
        await runtime.bus.drain()
        operation = runtime.bus.read_operation(str(accepted["operation_id"]))
        assert operation is not None and operation.state.value == "FAILED"
        heads = runtime.ledger.read_heads("p")
        assert not any(":requirements:" in key or ":resolved:" in key for key in heads)
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_completed_result_is_stale_after_policy_revision_changes(tmp_path: Path):
    runtime = await setup(tmp_path, ControlledResearchModel(), source=False)
    try:
        accepted = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "start",
                    {"project_id": "p", "problem": "Check current request", "contract_version": 2},
                )
            )
        )
        await runtime.bus.drain()
        policy = value(
            await runtime.bus.dispatch(
                request("project/policy/read", "policy", {"project_id": "p"})
            )
        )
        current = policy.get("policy", policy)
        project = value(
            await runtime.bus.dispatch(
                request("project/read", "project-revision", {"project_id": "p"})
            )
        )
        value(
            await runtime.bus.dispatch(
                request(
                    "project/policy/update",
                    "change",
                    {
                        "project_id": "p",
                        "expected_revision": project["revision"],
                        "payload": {**current["payload"], "audit_note": "changed"},
                    },
                )
            )
        )
        read = value(
            await runtime.bus.dispatch(
                request(
                    "thread/read", "read", {"project_id": "p", "thread_id": accepted["thread_id"]}
                )
            )
        )
        assert read["freshness"] != "CURRENT" and read["current_result"] is None
        assert read["previous_result"] is not None
    finally:
        runtime.close()
