"""Revalidate memory preparation authority against current project/session records."""

from __future__ import annotations

from pydantic import AwareDatetime

from thoth.domain.actor import ActorRef
from thoth.domain.auth import authenticated_data_scope_allows, current_authenticated_actor
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.enums import ProjectLifecycle
from thoth.domain.memory_preparation import MemoryAuthorityBasis
from thoth.ports.auth import AuthSessionStorePort
from thoth.ports.governance import GovernanceStorePort
from thoth.ports.project import ProjectStorePort
from thoth.ports.runtime import ClockPort


class MemoryAdmissionService:
    def __init__(
        self,
        *,
        projects: ProjectStorePort,
        governance: GovernanceStorePort,
        sessions: AuthSessionStorePort,
        clock: ClockPort,
    ) -> None:
        self._projects = projects
        self._governance = governance
        self._sessions = sessions
        self._clock = clock

    def capture(
        self,
        *,
        project_id: str,
        cutoff_at: AwareDatetime,
        scope: dict[str, str],
        actor: ActorRef | None,
    ) -> MemoryAuthorityBasis:
        project = self._projects.read(project_id)
        policy = self._governance.read_policy(project_id)
        if (
            project is None
            or policy is None
            or policy.project_id != project_id
            or project.policy_binding_ref != policy.policy_id
            or project.cutoff_at != cutoff_at
            or project.lifecycle
            in {
                ProjectLifecycle.CLOSING,
                ProjectLifecycle.ARCHIVED_READ_ONLY,
            }
        ):
            raise ValueError("MEMORY_PROJECT_POLICY_UNAVAILABLE")
        expected_policy_digest = domain_digest(
            "PROJECT_POLICY",
            "1.0.0",
            canonical_payload({"project_id": project_id, "policy": policy.payload}),
        )
        if policy.policy_digest != expected_policy_digest:
            raise ValueError("MEMORY_POLICY_DIGEST_MISMATCH")
        current = current_authenticated_actor()
        role_digest = session_digest = None
        if actor is not None and actor.project_id not in {None, project_id}:
            raise ValueError("MEMORY_ACTOR_PROJECT_MISMATCH")
        if current is None:
            if actor is not None and (
                actor.session_id is not None or actor.role_assignment_ref is not None
            ):
                raise ValueError("MEMORY_SESSION_CONTEXT_MISSING")
        else:
            session = self._sessions.read(current.session_id)
            role = next(
                (
                    item
                    for item in self._governance.list_roles(project_id)
                    if item.role_assignment_id == current.role_assignment_id
                ),
                None,
            )
            if (
                current.project_id != project_id
                or session is None
                or session.state != "ACTIVE"
                or session.revoked_at is not None
                or session.expires_at <= self._clock.now()
                or session.created_at > self._clock.now()
                or session.actor_id != current.actor_id
                or session.project_id != project_id
                or session.role_assignment_id != current.role_assignment_id
                or session.role != current.role
                or session.capabilities != current.capabilities
                or session.data_scopes != current.data_scopes
                or role is None
                or role.state != "ACTIVE"
                or role.project_id != project_id
                or role.actor_id != current.actor_id
                or role.role != current.role
                or (role.scope,) != current.data_scopes
                or tuple(
                    sorted(
                        tag.removeprefix("CAP_")
                        for tag in role.authority_tags
                        if tag.startswith("CAP_")
                    )
                )
                != current.capabilities
                or "WRITE" not in current.capabilities
                or not authenticated_data_scope_allows(scope)
            ):
                raise ValueError("MEMORY_AUTHORITY_NOT_CURRENT")
            # ActorRef can identify the trusted agent acting for this authenticated initiator.
            if actor is not None and (
                actor.session_id != current.session_id
                or actor.role_assignment_ref != current.role_assignment_id
            ):
                raise ValueError("MEMORY_ACTOR_SESSION_MISMATCH")
            role_digest = domain_digest(
                "MEMORY_ROLE_BASIS",
                "1.0.0",
                canonical_payload(role),
            )
            session_digest = domain_digest(
                "MEMORY_SESSION_BASIS",
                "1.0.0",
                canonical_payload(session.model_dump(mode="python", exclude={"token_digest"})),
            )
        return MemoryAuthorityBasis(
            project_digest=domain_digest(
                "MEMORY_PROJECT_BASIS",
                "1.0.0",
                canonical_payload(project),
            ),
            policy_id=policy.policy_id,
            policy_revision=policy.version,
            policy_digest=policy.policy_digest,
            policy_record_digest=domain_digest(
                "MEMORY_POLICY_BASIS",
                "1.0.0",
                canonical_payload(policy),
            ),
            actor_context=current,
            role_digest=role_digest,
            session_digest=session_digest,
        )
