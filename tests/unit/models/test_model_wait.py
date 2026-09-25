import asyncio
from time import monotonic

import pytest

from thoth.application.services import model_wait
from thoth.application.services.model_wait import await_current_model
from thoth.domain.research_lease import ResearchLeaseLost


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout", [None, 5.0])
async def test_live_boundary_recheck_fences_a_waiting_call(
    monkeypatch: pytest.MonkeyPatch, timeout: float | None
):
    monkeypatch.setattr(model_wait, "BOUNDARY_POLL_SECONDS", 0.01)
    stopped = asyncio.Event()
    checks = 0

    async def invoke() -> str:
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
        return "unreachable"

    def check() -> None:
        nonlocal checks
        checks += 1
        if checks == 3:
            raise ResearchLeaseLost("ATTEMPT_LEASE_FENCED")

    with pytest.raises(ResearchLeaseLost):
        await await_current_model(invoke, check, timeout)
    assert checks == 3 and stopped.is_set()


@pytest.mark.asyncio
async def test_deadline_is_bounded_even_if_adapter_ignores_initial_cancel(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(model_wait, "CANCEL_DRAIN_SECONDS", 0.03)
    release, finished = asyncio.Event(), asyncio.Event()

    async def stubborn() -> str:
        try:
            await release.wait()
        except asyncio.CancelledError:
            await release.wait()
        finished.set()
        return "must not become a current result"

    started = monotonic()
    try:
        with pytest.raises(TimeoutError):
            await await_current_model(stubborn, lambda: None, 0.02)
        assert monotonic() - started < 0.5
    finally:
        release.set()
        await asyncio.wait_for(finished.wait(), 1)


@pytest.mark.asyncio
async def test_model_result_preserves_the_selected_deadline():
    async def invoke() -> str:
        await asyncio.sleep(0)
        return "result"

    assert await await_current_model(invoke, lambda: None, 600) == "result"
