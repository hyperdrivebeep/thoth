"""Scope lookups shared only inside an explicit pure-query read transaction."""

import asyncio
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from thoth.domain.auth import AuthenticatedActorContext
from thoth.domain.resource_scope import ResourceScopeRecord


@dataclass
class ScopeReadContext:
    project_id: str
    actor: AuthenticatedActorContext | None
    alias_to_canonical: dict[str, str] = field(default_factory=dict)
    records: dict[str, ResourceScopeRecord] = field(default_factory=dict)
    checked: set[str] = field(default_factory=set)
    ancestors: set[str] = field(default_factory=set)


@dataclass
class ReadLookupMemo:
    task: asyncio.Task[object] | None
    active: bool = True
    lookups: dict[tuple[int, str, str], ScopeReadContext] = field(default_factory=dict)


_READ_LOOKUPS: ContextVar[ReadLookupMemo | None] = ContextVar("scope_read_lookups", default=None)


@contextmanager
def scope_read_transaction() -> Generator[None]:
    """Caller holds one outer read UoW; no memo exists for mutation/RPC callers."""
    from thoth.application.services.historical_access_verification import (
        historical_verification_allowed,
    )

    if not historical_verification_allowed():
        yield
        return
    memo = ReadLookupMemo(asyncio.current_task())
    token = _READ_LOOKUPS.set(memo)
    try:
        yield
    finally:
        memo.active = False
        memo.lookups.clear()
        _READ_LOOKUPS.reset(token)


def scope_read_context(
    owner: object, project: str, actor: AuthenticatedActorContext | None
) -> ScopeReadContext:
    """Reuse immutable lookups, never another admission's permission/depth verdict."""
    from thoth.application.services.historical_access_verification import (
        historical_verification_allowed,
    )

    memo = _READ_LOOKUPS.get()
    if (
        memo is None
        or not memo.active
        or not historical_verification_allowed()
        or memo.task is not asyncio.current_task()
    ):
        return ScopeReadContext(project, actor)
    key = (id(owner), project, "LOCAL" if actor is None else actor.model_dump_json())
    prior = memo.lookups.setdefault(key, ScopeReadContext(project, actor))
    return ScopeReadContext(
        project, actor, alias_to_canonical=prior.alias_to_canonical, records=prior.records
    )
