import asyncio

import pytest

from thoth.application.services.historical_access_verification import historical_query_verification
from thoth.application.services.resource_scope_read_context import (
    scope_read_context,
    scope_read_transaction,
)
from thoth.domain.auth import AuthenticatedActorContext
from thoth.domain.resource_scope import resource_use_scope


async def test_lookup_memo_is_query_transaction_task_project_and_owner_local():
    owner = object()
    other = object()
    with historical_query_verification(), resource_use_scope("p"):
        with scope_read_transaction():
            first = scope_read_context(owner, "p", None)
            first.alias_to_canonical["alias"] = "canonical"
            first.checked.add("checked")
            again = scope_read_context(owner, "p", None)
            assert again.alias_to_canonical is first.alias_to_canonical
            assert not again.checked and not again.ancestors
            assert not scope_read_context(other, "p", None).alias_to_canonical
            assert not scope_read_context(owner, "foreign", None).alias_to_canonical
            actor = AuthenticatedActorContext(
                actor_id="actor:one",
                session_id="session:one",
                project_id="p",
                role_assignment_id="role:one",
                role="reviewer",
                capabilities=("READ",),
                data_scopes=("PROJECT",),
            )
            assert not scope_read_context(owner, "p", actor).alias_to_canonical
            scope_read_context(owner, "p", actor).alias_to_canonical["actor-only"] = "canonical"
            assert not scope_read_context(
                owner, "p", actor.model_copy(update={"session_id": "another-session"})
            ).alias_to_canonical

            async def child():
                return scope_read_context(owner, "p", None).alias_to_canonical

            assert not await asyncio.create_task(child())
            assert not await asyncio.to_thread(
                lambda: scope_read_context(owner, "p", None).alias_to_canonical
            )
        with scope_read_transaction():
            assert not scope_read_context(owner, "p", None).alias_to_canonical
    with scope_read_transaction():
        assert not scope_read_context(owner, "p", None).alias_to_canonical


async def test_lookup_memo_exception_and_cancellation_cleanup():
    owner = object()
    with pytest.raises(RuntimeError), historical_query_verification(), scope_read_transaction():
        scope_read_context(owner, "p", None).alias_to_canonical["alias"] = "canonical"
        raise RuntimeError("fixture")
    entered = asyncio.Event()

    async def cancelled():
        with historical_query_verification(), scope_read_transaction():
            scope_read_context(owner, "p", None).alias_to_canonical["alias"] = "canonical"
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(cancelled())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with historical_query_verification(), scope_read_transaction():
        assert not scope_read_context(owner, "p", None).alias_to_canonical
