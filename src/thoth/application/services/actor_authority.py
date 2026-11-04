"""Shared current actor verification; preserves store/role/clock observation order."""

from thoth.domain.auth import AuthenticatedActorContext, current_authenticated_actor
from thoth.domain.resource_scope import ResourceScopeError
from thoth.ports.auth import AuthSessionStorePort
from thoth.ports.governance import GovernanceStorePort
from thoth.ports.runtime import ClockPort


def current_actor_for(
    project_id: str,
    sessions: AuthSessionStorePort,
    governance: GovernanceStorePort,
    clock: ClockPort,
) -> AuthenticatedActorContext | None:
    actor = current_authenticated_actor()
    if actor is None:
        return None
    session = sessions.read(actor.session_id)
    role = next(
        (
            r
            for r in governance.list_roles(project_id)
            if r.role_assignment_id == actor.role_assignment_id
        ),
        None,
    )
    if (
        actor.project_id != project_id
        or session is None
        or role is None
        or session.state != "ACTIVE"
        or session.expires_at <= clock.now()
        or session.project_id != project_id
        or session.actor_id != actor.actor_id
        or session.role_assignment_id != actor.role_assignment_id
        or session.role != actor.role
        or session.capabilities != actor.capabilities
        or session.data_scopes != actor.data_scopes
        or role.state != "ACTIVE"
        or role.actor_id != actor.actor_id
        or role.role != actor.role
        or (role.scope,) != actor.data_scopes
        or tuple(
            sorted(
                tag.removeprefix("CAP_") for tag in role.authority_tags if tag.startswith("CAP_")
            )
        )
        != actor.capabilities
    ):
        raise ResourceScopeError("RESOURCE_AUTHORITY_CHANGED")
    return actor
