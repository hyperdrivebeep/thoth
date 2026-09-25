from __future__ import annotations

from typing import Protocol

from thoth.domain.auth import AuthenticatedActorContext, AuthSession, IssuedAuthSession


class AuthSessionStorePort(Protocol):
    def put(self, value: AuthSession) -> None: ...
    def read_by_token_digest(self, token_digest: str) -> AuthSession | None: ...
    def read(self, session_id: str) -> AuthSession | None: ...


class CredentialVerifierPort(Protocol):
    def verify(self, actor_id: str, credential: str) -> bool: ...


class SessionTokenIssuerPort(Protocol):
    def issue(self) -> tuple[str, str]: ...
    def digest(self, token: str) -> str: ...


class HttpAuthenticationPort(Protocol):
    async def issue_http_session(
        self,
        *,
        actor_id: str,
        credential: str,
        project_id: str,
        role_assignment_id: str,
    ) -> IssuedAuthSession: ...

    async def authenticate_http(
        self,
        *,
        authorization: str | None,
        project_id: str,
        method: str,
        requested_scope: dict[str, str],
    ) -> AuthenticatedActorContext: ...
