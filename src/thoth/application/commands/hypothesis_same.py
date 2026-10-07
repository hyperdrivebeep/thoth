"""hypothesis/same/record and hypothesis/same/list.

A person marks two hypotheses of different investigations as the same hypothesis, and may take the
mark back. The marks are only a reference: nothing about either hypothesis is changed.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, JsonValue

from thoth.application.commands.verification_trace import require_trace_project
from thoth.application.services.human_actor import RECORD_HUMAN_ONLY
from thoth.application.services.hypothesis_same import HypothesisSame, SameRefused
from thoth.domain.auth import current_authenticated_actor
from thoth.domain.base import DomainModel
from thoth.domain.enums import ProjectLifecycle
from thoth.ports.project import ProjectStorePort
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode


class SameListInput(DomainModel):
    project_id: str = Field(min_length=1, max_length=160)


class SameRecordInput(SameListInput):
    hypothesis_ids: tuple[str, str]
    action: Literal["LINK", "UNLINK"]
    note: str = Field(default="", max_length=500)


class SameHandlers:
    def __init__(self, *, same: HypothesisSame, projects: ProjectStorePort) -> None:
        self._same, self._projects = same, projects

    def authorize_before_claim(self, method: str, value: dict[str, JsonValue]) -> None:
        project = require_trace_project(self._projects, str(value.get("project_id", "")))
        closed = {ProjectLifecycle.CLOSING, ProjectLifecycle.ARCHIVED_READ_ONLY}
        if method == "hypothesis/same/record" and project.lifecycle in closed:
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED, "closing or archived project cannot change a mark"
            )

    async def list(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = SameListInput.model_validate(value)
        self.authorize_before_claim("hypothesis/same/list", value)
        return self._same.view(request.project_id)  # type: ignore[return-value]

    async def record(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = SameRecordInput.model_validate(value)
        self.authorize_before_claim("hypothesis/same/record", value)
        actor = current_authenticated_actor()
        try:
            event = self._same.record(
                project_id=request.project_id,
                hypothesis_ids=request.hypothesis_ids,
                action=request.action,
                note=request.note,
                actor_id="human:local-user" if actor is None else actor.actor_id,
            )
        except (SameRefused, PermissionError) as exc:
            code = (
                RpcErrorCode.AUTHORIZATION_DENIED
                if str(exc) == RECORD_HUMAN_ONLY
                else RpcErrorCode.DOMAIN_REJECTED
            )
            raise RpcApplicationError(code, str(exc)) from exc
        return {
            "event": event.model_dump(mode="json"),
            **self._same.view(request.project_id),  # type: ignore[dict-item]
        }
