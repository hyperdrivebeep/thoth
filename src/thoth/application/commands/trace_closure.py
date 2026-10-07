"""trace/closure/record and trace/closure/list.

A person records that a row that was not met was closed some way outside the system, with the
document that says so. The verdict is not changed; the closure is shown beside it.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, JsonValue

from thoth.application.commands.verification_trace import require_trace_project
from thoth.application.services.human_actor import RECORD_HUMAN_ONLY
from thoth.application.services.trace_closure import ClosureRefused, TraceClosures
from thoth.domain.auth import current_authenticated_actor
from thoth.domain.base import DomainModel
from thoth.domain.enums import ProjectLifecycle
from thoth.domain.trace_closure import ClosureKind
from thoth.domain.verification_trace import SubjectKind
from thoth.ports.project import ProjectStorePort
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode


class ClosureListInput(DomainModel):
    project_id: str = Field(min_length=1, max_length=160)


class ClosureRecordInput(ClosureListInput):
    subject_kind: Literal["CRITERION", "REQUIREMENT"]
    subject_id: str = Field(min_length=1, max_length=500)
    kind: ClosureKind
    basis_ref: str = Field(min_length=1, max_length=500)
    note: str = Field(default="", max_length=2_000)
    scope: str | None = Field(default=None, max_length=500)
    # The verdict revision the person was looking at, so a change that came in meanwhile is not
    # closed on their behalf.
    current_verdict_revision: str = Field(min_length=64, max_length=64)


class ClosureHandlers:
    def __init__(self, *, closures: TraceClosures, projects: ProjectStorePort) -> None:
        self._closures, self._projects = closures, projects

    def authorize_before_claim(self, method: str, value: dict[str, JsonValue]) -> None:
        project = require_trace_project(self._projects, str(value.get("project_id", "")))
        closed = {ProjectLifecycle.CLOSING, ProjectLifecycle.ARCHIVED_READ_ONLY}
        if method == "trace/closure/record" and project.lifecycle in closed:
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED, "closing or archived project cannot change a closure"
            )

    async def list(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ClosureListInput.model_validate(value)
        self.authorize_before_claim("trace/closure/list", value)
        return {"closures": self._closures.view(request.project_id)}  # type: ignore[dict-item]

    async def record(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ClosureRecordInput.model_validate(value)
        self.authorize_before_claim("trace/closure/record", value)
        actor = current_authenticated_actor()
        try:
            event = self._closures.record(
                project_id=request.project_id,
                subject_kind=SubjectKind(request.subject_kind),
                subject_id=request.subject_id,
                kind=request.kind,
                basis_ref=request.basis_ref,
                note=request.note,
                scope=request.scope,
                current_verdict_revision=request.current_verdict_revision,
                actor_id="human:local-user" if actor is None else actor.actor_id,
            )
        except (ClosureRefused, PermissionError) as exc:
            code = (
                RpcErrorCode.AUTHORIZATION_DENIED
                if str(exc) == RECORD_HUMAN_ONLY
                else RpcErrorCode.DOMAIN_REJECTED
            )
            raise RpcApplicationError(code, str(exc)) from exc
        return {
            "event": event.model_dump(mode="json"),
            "closures": self._closures.view(request.project_id),  # type: ignore[dict-item]
        }
