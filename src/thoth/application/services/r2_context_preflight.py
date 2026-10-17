"""Pure R2 context decisions; reads and execution remain with the caller."""

from __future__ import annotations

from typing import TYPE_CHECKING

from thoth.application.services.r2_closed_loop import R2ClosedLoopExecution
from thoth.domain.canonical import same_stored_instant
from thoth.domain.enums import ProjectLifecycle

if TYPE_CHECKING:
    from thoth.domain.auth import AuthenticatedActorContext
    from thoth.domain.governance import RoleAssignment
    from thoth.domain.project import Project


def r2_project_context_hold(
    project: Project, current: Project | None
) -> Project | R2ClosedLoopExecution:
    if (
        current is None
        or not same_stored_instant(current.cutoff_at, project.cutoff_at)
        or current.policy_binding_ref != project.policy_binding_ref
        or current.overlay != project.overlay
        or current.lifecycle in {ProjectLifecycle.CLOSING, ProjectLifecycle.ARCHIVED_READ_ONLY}
    ):
        return R2ClosedLoopExecution(
            projection={
                "terminal_state": "HOLD",
                "reason_code": "R2_PROJECT_CONTEXT_CHANGED",
                "scientific_truth_state": "NOT_CERTIFIED",
                "pre_io": True,
            }
        )
    return current


def r2_authority_hold(
    authenticated: AuthenticatedActorContext,
    roles: tuple[RoleAssignment, ...],
) -> R2ClosedLoopExecution | None:
    role = next(
        (item for item in roles if item.role_assignment_id == authenticated.role_assignment_id),
        None,
    )
    if (
        role is None
        or role.state != "ACTIVE"
        or role.actor_id != authenticated.actor_id
        or role.role != authenticated.role
        or role.scope not in authenticated.data_scopes
    ):
        return R2ClosedLoopExecution(
            projection={
                "terminal_state": "AUTHORITY_REQUIRED",
                "reason_code": "R2_AUTHORITY_CHANGED",
                "scientific_truth_state": "NOT_CERTIFIED",
                "pre_io": True,
            }
        )
    return None
