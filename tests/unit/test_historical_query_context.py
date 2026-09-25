import asyncio

import pytest

from thoth.application.services.historical_access_verification import (
    historical_query_verification,
    historical_verification_allowed,
)
from thoth.domain.resource_scope import (
    ResourceScopeError,
    current_resource_uses,
    record_resource_use,
    resource_use_scope,
    resource_use_verification,
)


async def test_query_verification_is_task_local_and_cleans_up_after_exception():
    assert not historical_verification_allowed()

    async def child():
        return historical_verification_allowed()

    with pytest.raises(RuntimeError, match="fixture"), historical_query_verification():
        assert historical_verification_allowed()
        assert not await asyncio.create_task(child())
        assert not await asyncio.to_thread(historical_verification_allowed)
        raise RuntimeError("fixture")
    assert not historical_verification_allowed()
    with resource_use_scope("p"), historical_query_verification():
        assert (
            not historical_verification_allowed()
        )  # an existing consumer/mutation cannot borrow it


async def test_query_cancellation_does_not_leave_a_verification_mode():
    entered = asyncio.Event()

    async def cancelled():
        with historical_query_verification():
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(cancelled())
    await entered.wait()
    assert not historical_verification_allowed()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not historical_verification_allowed()


def test_verification_ledger_keeps_same_cap_and_restores_actual_reads_on_failure():
    with resource_use_scope("p"):
        record_resource_use("p", "actual:first", "READ")
        before = current_resource_uses()
        with (
            pytest.raises(ResourceScopeError, match="RESOURCE_SCOPE_USAGE_LIMIT"),
            resource_use_verification("p"),
        ):
            for index in range(4097):
                record_resource_use("p", f"historical:{index}", "READ")
        assert current_resource_uses() == before
        record_resource_use("p", "actual:second", "READ")
        uses = current_resource_uses()
        assert uses is not None and len(uses) == 2
        with (
            pytest.raises(ResourceScopeError, match="RESOURCE_SCOPE_PROJECT_MISMATCH"),
            resource_use_verification("another-project"),
        ):
            pass
