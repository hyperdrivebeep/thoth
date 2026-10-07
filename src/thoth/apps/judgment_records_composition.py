"""Compose the judgment records: test results, refutation conditions and trace closures."""

from __future__ import annotations

import functools

from pydantic import JsonValue

from thoth.application.commands.discrimination import DiscriminationHandlers
from thoth.application.commands.hypothesis_same import SameHandlers
from thoth.application.commands.lesson import LessonHandlers
from thoth.application.commands.trace_closure import ClosureHandlers
from thoth.application.services.discrimination_ledger import DiscriminationLedger
from thoth.application.services.hypothesis_same import HypothesisSame
from thoth.application.services.lesson_ledger import LessonLedger
from thoth.application.services.request_records import RequestRecords
from thoth.application.services.trace_closure import TraceClosures
from thoth.ports.project import ProjectStorePort
from thoth.protocol.registry import CommandHandler, MethodRegistry


def install_judgment_records(
    registry: MethodRegistry, records: RequestRecords, projects: ProjectStorePort
) -> None:
    discrimination = DiscriminationHandlers(ledger=DiscriminationLedger(records), projects=projects)
    registry.register("hypothesis/test/result/list", discrimination.list)
    registry.register("hypothesis/test/result/record", discrimination.record_result)
    registry.register("hypothesis/refutation/record", discrimination.record_conditions)
    trace_closures = TraceClosures(records)
    closures = ClosureHandlers(closures=trace_closures, projects=projects)
    registry.register("trace/closure/list", closures.list)
    registry.register("trace/closure/record", closures.record)
    lessons = LessonLedger(records)
    same = SameHandlers(same=HypothesisSame(records), projects=projects)
    registry.register("hypothesis/same/list", same.list)
    registry.register("hypothesis/same/record", same.record)
    registry.register(
        "trace/lesson/list",
        LessonHandlers(lessons=lessons, closures=trace_closures, projects=projects).list,
    )
    registry.decorate(
        "trace/importApply", lambda inner: _lessons_after_import(inner, lessons, trace_closures)
    )


def _lessons_after_import(
    inner: CommandHandler, lessons: LessonLedger, closures: TraceClosures
) -> CommandHandler:
    """New results may confirm the effect of a recorded fix: a lesson, written right after."""

    @functools.wraps(inner)
    async def applied(value: dict[str, JsonValue]):
        outcome = await inner(value)
        project_id = value.get("project_id")
        if isinstance(project_id, str) and isinstance(outcome, dict):
            lessons.sync_effects(project_id, closures.view(project_id))
        return outcome

    owner = getattr(inner, "__self__", None)
    if owner is not None:
        applied.__self__ = owner  # type: ignore[attr-defined]
    return applied
