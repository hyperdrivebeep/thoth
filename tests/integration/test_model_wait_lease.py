import asyncio
from datetime import timedelta
from pathlib import Path

import pytest
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_research_request_v2 import ControlledResearchModel, setup

from thoth.adapters.runtime import SystemClock
from thoth.application.services import model_wait
from thoth.application.services.research_leases import ResearchLeases
from thoth.domain.research_lease import ResearchLease


@pytest.mark.asyncio
async def test_normal_thread_renews_lease_while_model_awaits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(model_wait, "BOUNDARY_POLL_SECONDS", 0.03)
    original_now, original_validate = SystemClock.now, ResearchLeases.validate
    offset = timedelta()
    renewed = asyncio.Event()
    renewals: list[ResearchLease] = []

    def now(self: SystemClock):
        return original_now(self) + offset

    def validate(self: ResearchLeases, lease: ResearchLease) -> None:
        key = f"lease:{lease.thread_id}"
        before = self.records.journal_read(lease.project_id, key, ResearchLease)
        original_validate(self, lease)
        after = self.records.journal_read(lease.project_id, key, ResearchLease)
        if before and after and before.expires_at != after.expires_at:
            renewals.append(after)
            renewed.set()

    monkeypatch.setattr(SystemClock, "now", now)
    monkeypatch.setattr(ResearchLeases, "validate", validate)
    model = ControlledResearchModel(wait=True)
    runtime = await setup(tmp_path, model, source=False)
    try:
        admitted = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "start",
                    {
                        "project_id": "p",
                        "problem": "Observe a waiting model",
                        "contract_version": 2,
                    },
                )
            )
        )
        await asyncio.wait_for(model.started.wait(), 8)
        for _ in range(4):
            renewed.clear()
            offset += timedelta(seconds=100)
            await asyncio.wait_for(renewed.wait(), 3)
        assert len(renewals) >= 4
        assert len({lease.epoch for lease in renewals}) == 1
        assert len({lease.worker_id for lease in renewals}) == 1
        model.release.set()
        await asyncio.wait_for(runtime.bus.drain(), 30)
        status = value(
            await runtime.bus.query(
                request(
                    "thread/read", "read", {"project_id": "p", "thread_id": admitted["thread_id"]}
                )
            )
        )
        assert status["operation_state"] == "SUCCEEDED"
        assert status["current_result"]["completion"] == "TERMINAL"
        assert len(status["inputs"]) == 1
    finally:
        model.release.set()
        runtime.close()
