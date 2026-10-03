"""Reuse the completed stages of an interrupted research run, only when the user asked to resume.

A resume is a new operation that names the interrupted one. Each stage of the new run computes its
own input basis without the request revision; the stored stage is reused only when that digest is
exactly equal. Anything that changed since (sources, scope, settings, policy, cutoff, behavior)
changes the digest and that stage is called again. Nothing here runs without the user's action.
"""

from pydantic import BaseModel

from thoth.application.services.request_records import RequestRecords
from thoth.application.services.research_stages import (
    ReusedStage,
    persist_stage,
    read_stage,
    stage_bases,
)
from thoth.domain.enums import ModelRole
from thoth.domain.model import ModelRequest
from thoth.domain.research_execution import ResearchWork
from thoth.domain.research_request import ResearchAttempt, RevisionRef
from thoth.domain.research_stage import ReusedStageOrigin

# Roles whose output names this run's own requirement set and so cannot be carried to another run.
# A judgement review binds each conflict/gap decision to the requirement-set digest it was given
# (ConflictReviewDecision/GapReviewDecision.requirement_set_digest). The reuse basis leaves that
# reference out on purpose, so a reused judgement would carry the earlier run's digest and
# research_conflicts / research_gaps would then discard it as stale (NEEDS_REASSESSMENT,
# GAP_REQUIREMENT_BASIS_STALE). Such a stage is always called again.
NON_REUSABLE_ROLES = frozenset({ModelRole.REVIEW_ADJUDICATOR})


def find_reusable_stage[T: BaseModel](
    records: RequestRecords, work: ResearchWork, request: ModelRequest[T]
) -> ReusedStage | None:
    source_id = work.resume_from_operation_id
    if source_id is None or request.role in NON_REUSABLE_ROLES:
        return None
    attempt = records.journal_read(request.project_id, source_id, ResearchAttempt)
    if attempt is None or attempt.request_ref.project_id != request.project_id:
        return None
    wanted = stage_bases(work, request).reuse_basis
    for ref in attempt.completed_stage_refs:
        if ref.project_id != request.project_id or ref.revision_digest in work.reused_stage_sources:
            continue
        try:
            stage = read_stage(records, ref)
        except ValueError:
            continue
        if (
            stage.state == "COMPLETED"
            and stage.request_ref == attempt.request_ref
            and stage.role == request.role.value
            and stage.output_codec == request.output_model.__name__
            and stage.reuse_basis_digest == wanted
        ):
            origin = stage.reused_from or ReusedStageOrigin(stage_ref=ref, operation_id=source_id)
            work.reused_stage_sources.add(ref.revision_digest)
            return ReusedStage(stage, origin)
    return None


def reuse_stage_if_exact[T: BaseModel](
    records: RequestRecords, work: ResearchWork, request: ModelRequest[T]
) -> RevisionRef | None:
    """Record a reused stage for this run, or None when the stage must be called normally."""

    reused = find_reusable_stage(records, work, request)
    if reused is None:
        return None
    return persist_stage(records, work, request, None, 0, reused)
