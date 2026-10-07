"""hypothesis/link/list and hypothesis/link/recheck.

The list says, for each hypothesis that came from a trace row, whether that row's verdict has
changed since; the re-check is a person's recorded decision about one such change. No model is
called, and a hypothesis is never edited: the link is read against the trace every time.
"""

from __future__ import annotations

from pydantic import Field, JsonValue

from thoth.application.commands.verification_trace import require_trace_project
from thoth.application.services.hypothesis_link_recheck import (
    HypothesisLinkRechecks,
    RecheckRefused,
)
from thoth.application.services.hypothesis_link_view import HypothesisLinkReader, LinkItem
from thoth.domain.auth import current_authenticated_actor
from thoth.domain.base import DomainModel
from thoth.domain.enums import ProjectLifecycle
from thoth.domain.verdict_link import RecheckReason
from thoth.ports.project import ProjectStorePort
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode


class LinkListInput(DomainModel):
    project_id: str = Field(min_length=1, max_length=160)


class LinkRecheckInput(DomainModel):
    project_id: str = Field(min_length=1, max_length=160)
    hypothesis_ids: tuple[str, ...] = Field(min_length=1, max_length=50)
    reason_code: RecheckReason
    note: str = Field(default="", max_length=2_000)
    # The verdict revision the person was looking at (null when the row is gone), so that a change
    # that came in meanwhile is not accepted on their behalf.
    current_verdict_revision: str | None = Field(default=None, max_length=64)


def link_json(item: LinkItem) -> dict[str, JsonValue]:
    link, found = item.record.verdict_link, item.assessment
    assert link is not None
    event = found.recheck
    return {
        "hypothesis_id": item.hypothesis_id,
        "hypothesis_revision_digest": item.hypothesis_revision_digest,
        "statement": item.statement,
        "subject_kind": link.subject_kind,
        "subject_id": link.subject_id,
        "subject_title": item.subject_title,
        "state": found.state.value,
        "change": found.change,
        "link_state": found.link_state,
        "current_state": found.current_state,
        "current_verdict_revision": found.current_verdict_revision,
        "recheck": None
        if event is None
        else {
            "reason_code": event.reason_code,
            "note": event.note,
            "actor_id": event.actor_id,
            "created_at": event.created_at.isoformat(),
            "flipped": event.flipped,
        },
    }


class HypothesisLinkHandlers:
    def __init__(
        self,
        *,
        reader: HypothesisLinkReader,
        rechecks: HypothesisLinkRechecks,
        projects: ProjectStorePort,
    ) -> None:
        self._reader, self._rechecks, self._projects = reader, rechecks, projects

    def authorize_before_claim(self, method: str, value: dict[str, JsonValue]) -> None:
        """The project rule of trace/read; changing a re-check also needs the project to be open."""
        project = require_trace_project(self._projects, str(value.get("project_id", "")))
        closed = {ProjectLifecycle.CLOSING, ProjectLifecycle.ARCHIVED_READ_ONLY}
        if method == "hypothesis/link/recheck" and project.lifecycle in closed:
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED, "closing or archived project cannot change a re-check"
            )

    async def list(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = LinkListInput.model_validate(value)
        self.authorize_before_claim("hypothesis/link/list", value)
        trace_digest, _ = self._reader.trace(request.project_id)
        return {
            "trace_digest": trace_digest,
            "links": [link_json(item) for item in self._reader.items(request.project_id)],
            "reason_distribution": self._reader.reason_distribution(request.project_id),  # type: ignore[dict-item]
        }

    async def recheck(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = LinkRecheckInput.model_validate(value)
        self.authorize_before_claim("hypothesis/link/recheck", value)
        actor = current_authenticated_actor()
        try:
            events = self._rechecks.recheck(
                project_id=request.project_id,
                hypothesis_ids=request.hypothesis_ids,
                reason=request.reason_code,
                note=request.note,
                actor_id="human:local-user" if actor is None else actor.actor_id,
                current_verdict_revision=request.current_verdict_revision,
            )
        except RecheckRefused as exc:
            code = (
                RpcErrorCode.AUTHORIZATION_DENIED
                if str(exc) == "RECHECK_HUMAN_ONLY"
                else RpcErrorCode.DOMAIN_REJECTED
            )
            raise RpcApplicationError(code, str(exc)) from exc
        ids = tuple(event.hypothesis_id for event in events)
        return {
            "events": [event.model_dump(mode="json") for event in events],
            "links": [link_json(item) for item in self._reader.items(request.project_id, ids)],
            "reason_distribution": self._reader.reason_distribution(request.project_id),  # type: ignore[dict-item]
        }
