from __future__ import annotations

from typing import Protocol


class ModelCredentialError(RuntimeError):
    pass


class ModelCredentialPort(Protocol):
    def account_connections(self) -> list[dict[str, object]]: ...

    def list_credentials(self) -> tuple[dict[str, str], ...]: ...

    def register_api_key(
        self,
        *,
        provider: str,
        model: str,
        api_key: str,
        base_url: str,
    ) -> dict[str, str]: ...

    def start_login(self, provider: str, auth_method: str | None = None) -> dict[str, object]: ...

    def login_status(
        self, provider: str, login_id: str | None = None, auth_method: str | None = None
    ) -> dict[str, object]: ...

    def cancel_login(
        self, provider: str, login_id: str, auth_method: str | None = None
    ) -> dict[str, object]: ...

    def submit_login_response(
        self, provider: str, login_id: str, response: str, auth_method: str
    ) -> dict[str, object]: ...
