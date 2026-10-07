"""trace/read, trace/export, trace/importPreview, trace/importApply and trace/confirm.

The trace is a project's requirement-to-result table with verdicts computed by rule. These handlers
only carry the user's actions to the services: no model is called. Reads change nothing; a preview
saves nothing; an apply stores the new set and its verdicts in one write.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Literal

from pydantic import Field, JsonValue

from thoth.application.services.trace_csv import (
    FORMAT,
    LOSS_MANIFEST,
    MAX_CSV_CHARS,
    export_csv,
    input_sha256,
)
from thoth.application.services.verification_trace import (
    TraceConflict,
    TraceError,
    VerificationTraceService,
)
from thoth.application.services.verification_trace_import import (
    ImportRejected,
    TraceImportService,
)
from thoth.application.services.verification_trace_view import revision_json, trace_view
from thoth.domain.auth import current_authenticated_actor
from thoth.domain.base import DomainModel
from thoth.domain.enums import ProjectLifecycle
from thoth.domain.project import Project as ProjectRecord
from thoth.domain.verification_trace import SubjectKind, TraceSet
from thoth.ports.project import ProjectStorePort
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode

_STALE = ("TRACE_IMPORT_PREVIEW_STALE", "TRACE_IMPORT_INPUT_CHANGED")


def require_trace_project(projects: ProjectStorePort, project_id: str) -> ProjectRecord:
    """The project exists, and a signed-in user needs project scope (trace/read's rule)."""
    project = projects.read(project_id)
    if project is None:
        raise RpcApplicationError(RpcErrorCode.PROJECT_NOT_FOUND, "project not found")
    actor = current_authenticated_actor()
    if actor is not None and "PROJECT" not in actor.data_scopes:
        raise RpcApplicationError(
            RpcErrorCode.AUTHORIZATION_DENIED, "PROJECT_SETTINGS_SCOPE_REQUIRED"
        )
    return project


class TraceRead(DomainModel):
    project_id: str = Field(min_length=1, max_length=160)


class TraceHistoryRead(TraceRead):
    subject_kind: Literal["CRITERION", "REQUIREMENT"]
    subject_id: str = Field(min_length=1, max_length=500)
    before_digest: str | None = Field(default=None, min_length=64, max_length=64)
    limit: int = Field(default=20, ge=1, le=50)


class TraceImportPreview(TraceRead):
    mode: Literal["CREATE", "UPDATE"]
    csv_text: str = Field(min_length=1, max_length=MAX_CSV_CHARS)


class TraceImportApply(TraceImportPreview):
    preview_id: str = Field(min_length=64, max_length=64)
    input_sha256: str = Field(min_length=64, max_length=64)


class TraceConfirm(TraceRead):
    verdict_revision_digest: str = Field(min_length=64, max_length=64)
    rationale: str = Field(min_length=1, max_length=2000)
    expected_digest: str | None = Field(default=None, min_length=64, max_length=64)


class TraceHandlers:
    def __init__(
        self,
        *,
        service: VerificationTraceService,
        importer: TraceImportService,
        projects: ProjectStorePort,
        closure_notes: Callable[[str], Mapping[str, str]] | None = None,
    ) -> None:
        self._service, self._importer, self._projects = service, importer, projects
        self._closure_notes = closure_notes  # project id -> the closure_status note of each row

    def authorize_before_claim(self, method: str, value: dict[str, JsonValue]) -> None:
        """The same project rule as the model settings: the project exists, and a signed-in
        user needs project scope. Changing the trace also needs the project to be open."""
        project = require_trace_project(self._projects, str(value.get("project_id", "")))
        writes = method in {"trace/importApply", "trace/confirm"}
        closed = {ProjectLifecycle.CLOSING, ProjectLifecycle.ARCHIVED_READ_ONLY}
        if writes and project.lifecycle in closed:
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED, "closing or archived project cannot change the trace"
            )

    def _actor(self) -> str:
        actor = current_authenticated_actor()
        return "human:local-user" if actor is None else actor.actor_id

    def _view(self, project_id: str) -> dict[str, JsonValue]:
        digest, record = self._service.read(project_id)
        return trace_view(self._service, project_id, digest, record)

    async def read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = TraceRead.model_validate(value)
        self.authorize_before_claim("trace/read", value)
        return self._view(request.project_id)

    async def history(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        """Older revisions of one verdict, newest first, in pages."""
        request = TraceHistoryRead.model_validate(value)
        self.authorize_before_claim("trace/history", value)
        _, record = self._service.read(request.project_id)
        if record is None:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "TRACE_NOT_FOUND")
        try:
            page = self._service.history(
                request.project_id,
                record,
                SubjectKind(request.subject_kind),
                request.subject_id,
                before=request.before_digest,
                limit=request.limit,
            )
        except TraceError as exc:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc
        return {
            "subject_kind": request.subject_kind,
            "subject_id": request.subject_id,
            "history_total": page.total,
            "revisions": [revision_json(item) for item in page.revisions],
            "next_before_digest": page.next_before,
        }

    async def export(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = TraceRead.model_validate(value)
        self.authorize_before_claim("trace/export", value)
        _, record = self._service.read(request.project_id)
        trace_set = TraceSet() if record is None else record.trace_set
        text = export_csv(
            trace_set, self._closure_notes and self._closure_notes(request.project_id)
        )
        return {
            "format": FORMAT,
            "filename": "thoth-trace.csv",
            "csv_text": text,
            "sha256": input_sha256(text),
            "set_digest": trace_set.set_digest,
            "loss_manifest": list(LOSS_MANIFEST),
        }

    async def import_preview(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = TraceImportPreview.model_validate(value)
        self.authorize_before_claim("trace/importPreview", value)
        plan = self._importer.preview(request.project_id, request.mode, request.csv_text)
        return plan.as_json()

    async def import_apply(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = TraceImportApply.model_validate(value)
        self.authorize_before_claim("trace/importApply", value)
        try:
            result = self._importer.apply(
                request.project_id,
                request.mode,
                request.csv_text,
                request.preview_id,
                request.input_sha256,
                self._actor(),
            )
        except TraceError as exc:
            raise self._error(exc) from exc
        return {**result, "trace": self._view(request.project_id)}

    async def confirm(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = TraceConfirm.model_validate(value)
        self.authorize_before_claim("trace/confirm", value)
        try:
            self._service.confirm(
                request.project_id,
                request.verdict_revision_digest,
                self._actor(),
                request.rationale,
                request.expected_digest,
            )
        except TraceError as exc:
            raise self._error(exc) from exc
        return self._view(request.project_id)

    @staticmethod
    def _error(exc: TraceError) -> RpcApplicationError:
        if isinstance(exc, TraceConflict) or (
            isinstance(exc, ImportRejected) and str(exc) in _STALE
        ):
            return RpcApplicationError(RpcErrorCode.STALE_CHECKPOINT, str(exc))
        return RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc))
