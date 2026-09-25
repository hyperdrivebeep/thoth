from __future__ import annotations

from datetime import timedelta

from thoth.domain.auth import (
    AuthenticatedActorContext,
    AuthSession,
    IssuedAuthSession,
)
from thoth.ports.auth import (
    AuthSessionStorePort,
    CredentialVerifierPort,
    HttpAuthenticationPort,
    SessionTokenIssuerPort,
)
from thoth.ports.governance import GovernanceStorePort
from thoth.ports.runtime import ClockPort, IdGeneratorPort


class LocalAuthenticationService(HttpAuthenticationPort):
    def __init__(
        self,
        *,
        sessions: AuthSessionStorePort,
        governance: GovernanceStorePort,
        credentials: CredentialVerifierPort,
        tokens: SessionTokenIssuerPort,
        clock: ClockPort,
        ids: IdGeneratorPort,
        lifetime: timedelta = timedelta(hours=8),
    ) -> None:
        self._sessions = sessions
        self._governance = governance
        self._credentials = credentials
        self._tokens = tokens
        self._clock = clock
        self._ids = ids
        self._lifetime = lifetime

    async def issue_http_session(
        self,
        *,
        actor_id: str,
        credential: str,
        project_id: str,
        role_assignment_id: str,
    ) -> IssuedAuthSession:
        if not self._credentials.verify(actor_id, credential):
            raise PermissionError("AUTH_CREDENTIAL_INVALID")
        role = next(
            (
                item
                for item in self._governance.list_roles(project_id)
                if item.role_assignment_id == role_assignment_id
                and item.actor_id == actor_id
                and item.state == "ACTIVE"
            ),
            None,
        )
        if role is None:
            raise PermissionError("AUTH_ROLE_BINDING_INVALID")
        token, token_digest = self._tokens.issue()
        now = self._clock.now()
        capabilities = tuple(
            sorted(
                {
                    item.removeprefix("CAP_")
                    for item in role.authority_tags
                    if item.startswith("CAP_")
                }
            )
        )
        session = AuthSession(
            session_id=self._ids.new("auth-session"),
            actor_id=actor_id,
            project_id=project_id,
            role_assignment_id=role.role_assignment_id,
            role=role.role,
            capabilities=capabilities,
            data_scopes=(role.scope,),
            token_digest=token_digest,
            created_at=now,
            expires_at=now + self._lifetime,
        )
        self._sessions.put(session)
        return IssuedAuthSession(session=session, bearer_token=token)

    async def authenticate_http(
        self,
        *,
        authorization: str | None,
        project_id: str,
        method: str,
        requested_scope: dict[str, str],
    ) -> AuthenticatedActorContext:
        if authorization is None or not authorization.startswith("Bearer "):
            raise PermissionError("AUTH_SESSION_REQUIRED")
        token = authorization.removeprefix("Bearer ").strip()
        session = self._sessions.read_by_token_digest(self._tokens.digest(token))
        if session is None or session.state != "ACTIVE":
            raise PermissionError("AUTH_SESSION_INVALID")
        if session.expires_at <= self._clock.now():
            raise PermissionError("AUTH_SESSION_EXPIRED")
        if session.project_id != project_id:
            raise PermissionError("AUTH_PROJECT_SCOPE_DENIED")
        role = next(
            (
                item
                for item in self._governance.list_roles(project_id)
                if item.role_assignment_id == session.role_assignment_id
                and item.actor_id == session.actor_id
                and item.state == "ACTIVE"
            ),
            None,
        )
        current_capabilities = (
            ()
            if role is None
            else tuple(
                sorted(
                    tag.removeprefix("CAP_")
                    for tag in role.authority_tags
                    if tag.startswith("CAP_")
                )
            )
        )
        if (
            role is None
            or role.role != session.role
            or (role.scope,) != session.data_scopes
            or current_capabilities != session.capabilities
        ):
            raise PermissionError("AUTH_ROLE_BINDING_INVALID")
        required = self._required_capabilities(method)
        if not required.issubset(session.capabilities):
            raise PermissionError("AUTH_CAPABILITY_DENIED")
        workstream = requested_scope.get("workstream")
        if "PROJECT" not in session.data_scopes and (
            workstream is None or f"WORKSTREAM:{workstream}" not in session.data_scopes
        ):
            raise PermissionError("AUTH_DATA_SCOPE_DENIED")
        return AuthenticatedActorContext(
            actor_id=session.actor_id,
            session_id=session.session_id,
            project_id=session.project_id,
            role_assignment_id=session.role_assignment_id,
            role=session.role,
            capabilities=session.capabilities,
            data_scopes=session.data_scopes,
        )

    @staticmethod
    def _required_capabilities(method: str) -> frozenset[str]:
        read_only = method != "receipt/audit/read" and method.endswith(
            ("/read", "/list", "/status", "/audit", "/history")
        )
        read_only = read_only or method in {"revision/restore/preview", "revision/compare"}
        required = {"READ"} if read_only else {"WRITE"}
        if method.startswith("thread/"):
            required.add("THREAD")
        if method.startswith("revision/"):
            required.add("REVISION")
        if method.startswith("project/role/") or method.startswith("project/policy/"):
            required.add("ADMIN")
        return frozenset(required)
