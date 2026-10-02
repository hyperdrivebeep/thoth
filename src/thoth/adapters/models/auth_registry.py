"""Workspace-bound authentication handlers, separate from model execution routes."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from thoth.ports.model_credentials import ModelCredentialError


class AuthHandler(Protocol):
    def start_login(self) -> dict[str, object]: ...

    def login_status(self, login_id: str | None = None) -> dict[str, object]: ...

    def cancel_login(self, login_id: str) -> dict[str, object]: ...


@dataclass(frozen=True)
class AuthRoute:
    account_provider: str
    auth_method: str
    model_route: str
    factory: Callable[[Path], AuthHandler]
    manual_complete: bool = False


class AuthRegistry:
    def __init__(self) -> None:
        self._routes: dict[tuple[str, str], AuthRoute] = {}

    def register(self, route: AuthRoute) -> None:
        key = (route.account_provider, route.auth_method)
        if key in self._routes:
            raise ValueError("AUTH_ROUTE_DUPLICATE")
        self._routes[key] = route

    def resolve(self, provider: str, auth_method: str) -> AuthRoute:
        try:
            return self._routes[(provider, auth_method)]
        except KeyError as exc:
            raise ModelCredentialError("MODEL_ACCOUNT_LOGIN_UNSUPPORTED") from exc


def default_auth_registry() -> AuthRegistry:
    def codex(workspace: Path) -> AuthHandler:
        from thoth.adapters.models.codex_broker import broker_for_workspace

        return broker_for_workspace(workspace)

    def xai(workspace: Path) -> AuthHandler:
        from thoth.adapters.models.xai_broker import broker_for_workspace

        return broker_for_workspace(workspace)

    def claude(workspace: Path) -> AuthHandler:
        from thoth.adapters.models.claude_oauth import broker_for_workspace

        return broker_for_workspace(workspace)

    def claude_code(workspace: Path) -> AuthHandler:
        from thoth.adapters.models.claude_code_login import broker_for_workspace

        return broker_for_workspace(workspace)

    registry = AuthRegistry()
    registry.register(AuthRoute("openai", "codex_isolated_browser", "codex-oauth", codex))
    registry.register(AuthRoute("xai", "xai_device_code", "xai-oauth", xai))
    registry.register(AuthRoute("anthropic", "claude_pkce", "claude-oauth", claude, True))
    registry.register(AuthRoute("anthropic", "claude_code_login", "claude-code", claude_code, True))
    return registry
