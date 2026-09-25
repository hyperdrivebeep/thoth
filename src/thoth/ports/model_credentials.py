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

    def start_login(self, provider: str) -> dict[str, object]: ...
