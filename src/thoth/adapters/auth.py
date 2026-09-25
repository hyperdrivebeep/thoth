from __future__ import annotations

import hashlib
import hmac
import secrets

from thoth.ports.auth import CredentialVerifierPort, SessionTokenIssuerPort


class StaticCredentialVerifier(CredentialVerifierPort):
    def __init__(self, credential_digests: dict[str, str]) -> None:
        self._digests = dict(credential_digests)

    def verify(self, actor_id: str, credential: str) -> bool:
        expected = self._digests.get(actor_id)
        actual = hashlib.sha256(credential.encode()).hexdigest()
        return expected is not None and hmac.compare_digest(expected, actual)


class SecureSessionTokenIssuer(SessionTokenIssuerPort):
    def issue(self) -> tuple[str, str]:
        token = secrets.token_urlsafe(32)
        return token, self.digest(token)

    def digest(self, token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()
