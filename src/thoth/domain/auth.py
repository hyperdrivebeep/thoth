from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Literal

from pydantic import AwareDatetime

from thoth.domain.base import DomainModel
from thoth.domain.ids import ProjectId, Sha256


class AuthSession(DomainModel):
    session_id: str
    actor_id: str
    project_id: ProjectId
    role_assignment_id: str
    role: str
    capabilities: tuple[str, ...]
    data_scopes: tuple[str, ...]
    token_digest: Sha256
    state: Literal["ACTIVE", "REVOKED", "EXPIRED"] = "ACTIVE"
    created_at: AwareDatetime
    expires_at: AwareDatetime
    revoked_at: AwareDatetime | None = None
    schema_version: str = "1.0.0"


class IssuedAuthSession(DomainModel):
    session: AuthSession
    bearer_token: str


class AuthenticatedActorContext(DomainModel):
    actor_id: str
    session_id: str
    project_id: ProjectId
    role_assignment_id: str
    role: str
    capabilities: tuple[str, ...]
    data_scopes: tuple[str, ...]


class AuthorizationDenial(DomainModel):
    reason_code: str
    project_id: ProjectId | None = None
    method: str | None = None
    actor_id: str | None = None
    session_id: str | None = None
    pre_io: Literal[True] = True


_AUTH_CONTEXT: ContextVar[AuthenticatedActorContext | None] = ContextVar(
    "thoth_authenticated_actor",
    default=None,
)


def current_authenticated_actor() -> AuthenticatedActorContext | None:
    return _AUTH_CONTEXT.get()


def authenticated_authority_matches(
    project_id: str,
    actor_id: str,
    role_assignment_id: str,
) -> bool:
    current = current_authenticated_actor()
    return current is None or (
        current.project_id == project_id
        and current.actor_id == actor_id
        and current.role_assignment_id == role_assignment_id
    )


def require_authenticated_authority(
    project_id: str,
    actor_id: str,
    role_assignment_id: str,
) -> None:
    if not authenticated_authority_matches(project_id, actor_id, role_assignment_id):
        raise PermissionError("AUTH_AUTHORITY_IDENTITY_MISMATCH")


def bind_authenticated_actor(project_id: str, actor_id: str) -> str:
    current = current_authenticated_actor()
    if current is None:
        return actor_id
    if current.project_id != project_id or current.actor_id != actor_id:
        raise PermissionError("AUTH_ACTOR_IDENTITY_MISMATCH")
    return current.actor_id


def authenticated_data_scope_allows(scope: dict[str, str]) -> bool:
    current = current_authenticated_actor()
    if current is None or "PROJECT" in current.data_scopes:
        return True
    workstream = scope.get("workstream")
    return isinstance(workstream, str) and f"WORKSTREAM:{workstream}" in current.data_scopes


@contextmanager
def authenticated_actor_scope(
    value: AuthenticatedActorContext,
):
    token: Token[AuthenticatedActorContext | None] = _AUTH_CONTEXT.set(value)
    try:
        yield
    finally:
        _AUTH_CONTEXT.reset(token)
