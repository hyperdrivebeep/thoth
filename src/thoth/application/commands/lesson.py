"""trace/lesson/list: the lessons recalled for each trace row, from its exact context.

A read. A lesson is a reference to a record a rule computed or a person made; each row gets only the
lessons of its own criterion, unit, condition and rule revision, marked stale or refuted when they
are. Nothing is ranked or counted, and no model is called.
"""

from __future__ import annotations

from pydantic import Field, JsonValue

from thoth.application.commands.verification_trace import require_trace_project
from thoth.application.services.lesson_ledger import LessonLedger
from thoth.application.services.trace_closure import TraceClosures
from thoth.domain.base import DomainModel
from thoth.ports.project import ProjectStorePort


class LessonListInput(DomainModel):
    project_id: str = Field(min_length=1, max_length=160)


class LessonHandlers:
    def __init__(
        self, *, lessons: LessonLedger, closures: TraceClosures, projects: ProjectStorePort
    ) -> None:
        self._lessons, self._closures, self._projects = lessons, closures, projects

    def authorize_before_claim(self, method: str, value: dict[str, JsonValue]) -> None:
        require_trace_project(self._projects, str(value.get("project_id", "")))

    async def list(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = LessonListInput.model_validate(value)
        self.authorize_before_claim("trace/lesson/list", value)
        rows = self._closures.view(request.project_id)
        return {"lessons": self._lessons.recall(request.project_id, rows)}  # type: ignore[dict-item]
