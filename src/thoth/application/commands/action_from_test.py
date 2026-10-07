"""action/draft/fromTest: a draft action request made from one discriminating test.

It composes the parameters of action/create from the stored hypothesis and calls that method, so
every check and guard of action/create applies (a stale hypothesis is refused there). The client
sends only which test and what the person declares the action would do.
"""

from __future__ import annotations

from typing import cast

from pydantic import Field, JsonValue

from thoth.application.services.action_from_test import (
    HYPOTHESIS_NOT_FOUND,
    DraftRefused,
    draft_request,
)
from thoth.application.services.hypothesis_link_view import HypothesisLinkReader
from thoth.domain.base import DomainModel
from thoth.protocol.deferred import AcceptedRunning, EphemeralCommandResult, PendingExecution
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode
from thoth.protocol.registry import CommandHandler


class FromTestInput(DomainModel):
    project_id: str = Field(min_length=1, max_length=160)
    hypothesis_id: str = Field(min_length=1, max_length=200)
    test_id: str = Field(min_length=1, max_length=200)
    # The effect flags a person declares for the action; empty means "nothing declared".
    effect_declaration: dict[str, JsonValue] = Field(default_factory=dict)


class ActionFromTestHandlers:
    def __init__(self, *, reader: HypothesisLinkReader, create: CommandHandler) -> None:
        self._reader, self._create = reader, create

    async def draft(
        self, value: dict[str, JsonValue]
    ) -> dict[str, JsonValue] | AcceptedRunning | PendingExecution | EphemeralCommandResult:
        request = FromTestInput.model_validate(value)
        record = self._reader.hypothesis(request.project_id, request.hypothesis_id)
        if record is None:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, HYPOTHESIS_NOT_FOUND)
        try:
            parameters = draft_request(record, request.test_id, dict(request.effect_declaration))
        except DraftRefused as exc:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc
        create_input = cast(dict[str, JsonValue], {"project_id": request.project_id, **parameters})
        return await self._create(create_input)
