"""Compose the hypothesis link methods and the two places where a stale link is refused."""

from __future__ import annotations

import functools

from pydantic import JsonValue

from thoth.application.commands.hypothesis_link import HypothesisLinkHandlers
from thoth.application.services.hypothesis_link_recheck import HypothesisLinkRechecks
from thoth.application.services.hypothesis_link_view import (
    HYPOTHESIS_BASIS_CHANGED,
    HypothesisLinkReader,
)
from thoth.application.services.request_records import RequestRecords
from thoth.ports.project import ProjectStorePort
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode
from thoth.protocol.registry import CommandHandler, MethodRegistry


def install_hypothesis_links(
    registry: MethodRegistry, records: RequestRecords, projects: ProjectStorePort
) -> None:
    reader = HypothesisLinkReader(records.ledger)
    handlers = HypothesisLinkHandlers(
        reader=reader, rechecks=HypothesisLinkRechecks(records), projects=projects
    )
    registry.register("hypothesis/link/list", handlers.list)
    registry.register("hypothesis/link/recheck", handlers.recheck)
    registry.decorate(
        "action/authorization/prepare", lambda inner: _refuse_stale_plan(inner, reader)
    )
    for method in ("action/create", "action/generate"):
        registry.decorate(method, lambda inner: _refuse_stale_references(inner, reader))


def _refuse_stale_plan(inner: CommandHandler, reader: HypothesisLinkReader) -> CommandHandler:
    """Preparing an approval for a plan whose actions rest on a changed verdict is refused."""

    @functools.wraps(inner)
    async def guarded(value: dict[str, JsonValue]):
        project_id, digest = value.get("project_id"), value.get("plan_revision_digest")
        if isinstance(project_id, str) and isinstance(digest, str):
            try:
                reader.require_plan_revision_hypotheses_current(project_id, digest)
            except ValueError as exc:
                raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc
        return await inner(value)

    owner = getattr(inner, "__self__", None)
    if owner is not None:
        guarded.__self__ = owner  # type: ignore[attr-defined]
    return guarded


def _refuse_stale_references(inner: CommandHandler, reader: HypothesisLinkReader) -> CommandHandler:
    """A new action is not made on a hypothesis whose trace verdict has changed."""

    @functools.wraps(inner)
    async def guarded(value: dict[str, JsonValue]):
        project_id, references = value.get("project_id"), value.get("hypothesis_refs")
        named = tuple(str(item) for item in references) if isinstance(references, list) else ()
        if isinstance(project_id, str) and reader.stale_hypothesis_ids(project_id, named):
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, HYPOTHESIS_BASIS_CHANGED)
        return await inner(value)

    owner = getattr(inner, "__self__", None)
    if owner is not None:
        guarded.__self__ = owner  # type: ignore[attr-defined]
    return guarded


__all__ = ["HYPOTHESIS_BASIS_CHANGED", "install_hypothesis_links"]
