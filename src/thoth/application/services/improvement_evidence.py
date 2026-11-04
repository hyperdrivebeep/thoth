"""Resolve public assessments against existing owner-produced evaluation evidence."""

from __future__ import annotations

from thoth.domain.control_record import ControlRecord


def plan_matches_improvement(plan: ControlRecord, improvement: ControlRecord) -> bool:
    return (
        improvement.state == "PROPOSED"
        and plan.record_type == "EVALUATION_PLAN"
        and plan.project_id == improvement.project_id
        and plan.payload.get("improvement_revision_id") == improvement.record_id
        and plan.payload.get("improvement_record_digest") == improvement.record_digest
        and plan.payload.get("candidate_digest") == improvement.payload.get("candidate_digest")
        and plan.payload.get("baseline_digest") == improvement.payload.get("baseline_digest")
    )


def assessments_match(
    assessments: tuple[ControlRecord | None, ...],
    improvement: ControlRecord,
) -> bool:
    return bool(assessments) and all(
        item is not None
        and item.state == "EVALUATED"
        and plan_matches_improvement(item, improvement)
        and item.payload.get("promotion_eligible") is True
        for item in assessments
    )
