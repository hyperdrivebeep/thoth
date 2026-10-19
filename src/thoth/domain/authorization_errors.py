"""Typed rejection of an approval revision that cannot be consumed again."""

from thoth.domain.errors import DomainError


class AuthorizationAlreadyConsumed(DomainError):
    reason_code = "AUTHORIZATION_ALREADY_CONSUMED"

    def __init__(self) -> None:
        super().__init__(
            "이미 사용되었거나 최신 상태가 아닌 승인입니다. 승인을 다시 확인하세요. / "
            "This approval was already used or is no longer current. Check the approval again."
        )
