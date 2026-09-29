"""THOTH-owned model credentials. Secrets stay in the workspace file, never in RPC results."""

from __future__ import annotations

import json
import os
import subprocess
from contextlib import suppress
from pathlib import Path
from typing import cast

from openai import AsyncOpenAI

from thoth.adapters.models.anthropic_messages import AnthropicMessagesModel
from thoth.adapters.models.auth_registry import AuthRegistry, AuthRoute, default_auth_registry
from thoth.adapters.models.companies import COMPANIES, company
from thoth.adapters.models.openai_responses import OpenAIResponsesModel
from thoth.adapters.models.registry import RegisteredModelResolver
from thoth.domain.model_settings import ModelOption, ModelSelection
from thoth.ports.model import ModelExecutionHold, ModelPort
from thoth.ports.model_credentials import ModelCredentialError, ModelCredentialPort

_INDEX = "index.json"
_SECRETS = "secrets.json"


class LocalCredentialHold(ModelExecutionHold):
    pass


def registry_dir(root: Path | None = None) -> Path:
    base = root or Path(os.environ.get("THOTH_WORKSPACE", ".thoth"))
    return base.resolve() / "model-registry"


def _read_json(path: Path, *, encoding: str = "utf-8") -> dict[str, object]:
    try:
        raw: object = json.loads(path.read_text(encoding=encoding))
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {
        key: value for key, value in cast(dict[object, object], raw).items() if isinstance(key, str)
    }


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with suppress(OSError):
        os.chmod(path, 0o600)


def list_credentials(root: Path | None = None) -> tuple[dict[str, str], ...]:
    index = _read_json(registry_dir(root) / _INDEX)
    listed: list[dict[str, str]] = []
    for provider, block in index.items():
        if not isinstance(block, dict):
            continue
        values = cast(dict[object, object], block)
        models = values.get("models")
        if not isinstance(models, list):
            continue
        kind = str(values.get("kind") or "api_key")
        base = str(values.get("base_url") or "")
        for model in cast(list[object], models):
            if isinstance(model, str) and model:
                listed.append(
                    {
                        "provider": provider,
                        "model": model,
                        "kind": kind,
                        "base_url": base,
                    }
                )
    return tuple(listed)


def register_api_key(
    *,
    provider: str,
    model: str,
    api_key: str,
    base_url: str = "",
    root: Path | None = None,
) -> dict[str, str]:
    spec = company(provider)
    provider = (spec["provider"] if spec else provider).strip().lower()
    model = model.strip() or (spec["default_model"] if spec else "")
    api_key = api_key.strip()
    base_url = (base_url.strip() or (spec["base_url"] if spec else "")).rstrip("/")
    if not provider or not model or not api_key:
        raise LocalCredentialHold("MODEL_CREDENTIAL_INCOMPLETE")
    if not base_url.startswith("https://"):
        raise LocalCredentialHold("MODEL_CREDENTIAL_BASE_URL_INSECURE")
    directory = registry_dir(root)
    index_path = directory / _INDEX
    secrets_path = directory / _SECRETS
    index = _read_json(index_path)
    secrets = _read_json(secrets_path)
    block = index.get(provider)
    raw_models = (
        cast(dict[object, object], block).get("models") if isinstance(block, dict) else None
    )
    models: list[object] = (
        list(cast(list[object], raw_models)) if isinstance(raw_models, list) else []
    )
    if model not in models:
        models.append(model)
    index[provider] = {"kind": "api_key", "base_url": base_url, "models": models}
    secrets[provider] = api_key
    _write_json(index_path, index)
    _write_json(secrets_path, secrets)
    return {"provider": provider, "model": model, "kind": "api_key", "base_url": base_url}


def local_secret(provider: str, root: Path | None = None) -> str:
    secrets = _read_json(registry_dir(root) / _SECRETS)
    token = secrets.get(provider)
    if not isinstance(token, str) or not token:
        raise LocalCredentialHold("MODEL_CREDENTIAL_UNAVAILABLE")
    return token


def available_credentials(root: Path | None = None) -> tuple[dict[str, str], ...]:
    secrets = _read_json(registry_dir(root) / _SECRETS)
    return tuple(
        item
        for item in list_credentials(root)
        if isinstance((token := secrets.get(item["provider"])), str) and bool(token)
    )


class ThothLocalCatalog:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root

    def defaults(self) -> ModelSelection:
        options = self.options()
        if not options:
            return ModelSelection()
        option = options[0]
        return ModelSelection(
            provider=option.provider,
            model=option.model,
            reasoning_effort=option.default_effort,
        )

    def options(self) -> tuple[ModelOption, ...]:
        return tuple(
            ModelOption(
                provider=item["provider"],
                model=item["model"],
                reasoning_efforts=("low", "medium", "high"),
                default_effort="high",
                capability_source="thoth-local-credential/openai-responses-v1",
            )
            for item in available_credentials(self.root)
        )


def thoth_local_model(provider: str, model: str | None, root: Path | None = None) -> ModelPort:
    if not isinstance(model, str) or not model:
        raise LocalCredentialHold("MODEL_CREDENTIAL_MODEL_REQUIRED")
    match = next(
        (
            item
            for item in available_credentials(root)
            if item["provider"] == provider and item["model"] == model
        ),
        None,
    )
    if match is None:
        raise LocalCredentialHold("MODEL_CREDENTIAL_UNAVAILABLE")
    secret = local_secret(provider, root)
    if provider == "anthropic":
        return AnthropicMessagesModel(secret, model_id=model)
    client = AsyncOpenAI(api_key=secret, base_url=match["base_url"])
    return OpenAIResponsesModel(client, model_id=model)


def account_connections(
    codex_status: dict[str, object],
    root: Path | None = None,
    xai_status: dict[str, object] | None = None,
    claude_status: dict[str, object] | None = None,
) -> list[dict[str, object]]:
    listed = {item["provider"] for item in available_credentials(root)}
    rows: list[dict[str, object]] = []
    for key, spec in COMPANIES.items():
        oauth = key == "openai" and codex_status.get("connected") is True
        codex_eligible = key == "openai" and codex_status.get("execution_eligible") is True
        xai_oauth = key == "xai" and (xai_status or {}).get("connected") is True
        xai_eligible = key == "xai" and (xai_status or {}).get("execution_eligible") is True
        claude_oauth = key == "anthropic" and (claude_status or {}).get("connected") is True
        claude_eligible = (
            key == "anthropic" and (claude_status or {}).get("execution_eligible") is True
        )
        oauth_status = (
            codex_status
            if key == "openai"
            else (xai_status or {})
            if key == "xai"
            else (claude_status or {})
        )
        raw_capabilities = (claude_status or {}).get("capabilities")
        claude_capabilities: dict[str, object] = (
            cast(dict[str, object], raw_capabilities) if isinstance(raw_capabilities, dict) else {}
        )
        has_key = spec["provider"] in listed
        model_providers = [spec["provider"]] if has_key else []
        if codex_eligible:
            model_providers.append("codex-oauth")
        if xai_eligible:
            model_providers.append("xai-oauth")
        if claude_eligible:
            model_providers.append("claude-oauth")
        reason = (
            str(codex_status.get("reason_code") or "LOGIN_REQUIRED")
            if key == "openai" and not has_key
            else str((claude_status or {}).get("reason_code") or "MODEL_ACCOUNT_LOGIN_UNSUPPORTED")
            if key == "anthropic" and not has_key
            else "API_KEY_AVAILABLE"
            if has_key
            else "MODEL_ACCOUNT_LOGIN_UNSUPPORTED"
        )
        guidance = (
            "Use the saved THOTH workspace API key"
            if has_key
            else "Install the pinned Codex standalone package in the THOTH tools prefix"
            if reason in {"CODEX_STANDALONE_NOT_INSTALLED", "CODEX_STANDALONE_PIN_UNAVAILABLE"}
            else "Connect the THOTH-only Codex profile"
            if reason in {"LOGIN_REQUIRED", "LOGIN_PENDING"}
            else "Check the isolated Codex model catalog"
            if reason == "CATALOG_UNAVAILABLE"
            else "The Codex route is eligible; live execution has not been verified"
            if reason == "EXECUTION_UNVERIFIED"
            else "Use a THOTH workspace API key for this provider"
        )
        rows.append(
            {
                "provider": spec["provider"],
                "label": spec["label"],
                "kind": "account",
                "connected": oauth or xai_oauth or claude_oauth or has_key,
                "has_key": has_key,
                "oauth": oauth or xai_oauth or claude_oauth,
                "remote_auth_verified": None,
                "login_supported": key in {"openai", "xai"}
                or (key == "anthropic" and claude_capabilities.get("start") is True),
                "login_kind": "codex_isolated_browser"
                if key == "openai"
                else "xai_device_code"
                if key == "xai"
                else "claude_pkce"
                if claude_capabilities.get("start") is True
                else "unsupported",
                "available_model_providers": model_providers,
                "connection_state": (
                    codex_status.get("connection_state", "LOGIN_REQUIRED")
                    if key == "openai" and not has_key
                    else (xai_status or {}).get("connection_state", "LOGIN_REQUIRED")
                    if key == "xai" and not has_key
                    else (claude_status or {}).get("auth_state", "DISCONNECTED")
                    if key == "anthropic" and not has_key
                    else "API_KEY_AVAILABLE"
                    if has_key
                    else "UNAVAILABLE"
                ),
                "profile_mode": (
                    "THOTH_LOCAL_KEY"
                    if has_key
                    else "THOTH_ISOLATED"
                    if key == "openai"
                    else "THOTH_XAI_OAUTH"
                    if key == "xai"
                    else "THOTH_CLAUDE_OAUTH"
                    if key == "anthropic" and claude_oauth
                    else "THOTH_LOCAL_KEY"
                ),
                "reason_code": reason,
                "guidance": guidance,
                "execution_eligible": has_key or codex_eligible or xai_eligible or claude_eligible,
                "execution_verified": False,
                "auth_methods": [
                    {
                        "auth_method": "api_key",
                        "route": spec["provider"],
                        "connected": has_key,
                        "execution_eligible": has_key,
                        "connection_state": "API_KEY_AVAILABLE" if has_key else "LOGIN_REQUIRED",
                        "reason_code": "API_KEY_AVAILABLE"
                        if has_key
                        else "MODEL_CREDENTIAL_UNAVAILABLE",
                        "capabilities": {
                            "start": False,
                            "status": True,
                            "cancel": False,
                            "manual_complete": False,
                        },
                    },
                    {
                        "auth_method": "codex_isolated_browser"
                        if key == "openai"
                        else "xai_device_code"
                        if key == "xai"
                        else "claude_pkce",
                        "route": "codex-oauth"
                        if key == "openai"
                        else "xai-oauth"
                        if key == "xai"
                        else "claude-oauth",
                        "connected": oauth or xai_oauth or claude_oauth,
                        "execution_eligible": codex_eligible or xai_eligible or claude_eligible,
                        "connection_state": oauth_status.get(
                            "connection_state", oauth_status.get("auth_state", "LOGIN_REQUIRED")
                        ),
                        "reason_code": oauth_status.get("reason_code"),
                        "capabilities": {
                            "start": key != "anthropic" or claude_capabilities.get("start") is True,
                            "status": True,
                            "cancel": True,
                            "manual_complete": key == "anthropic",
                        },
                    },
                ],
            }
        )
        if key == "xai" and not has_key:
            rows[-1]["reason_code"] = (xai_status or {}).get("reason_code", "XAI_LOGIN_REQUIRED")
            rows[-1]["guidance"] = (
                "Use the THOTH workspace xAI device login; remote execution is unverified"
            )
    return rows


def start_codex_login(
    workspace: Path,
    *,
    wait_for_completion: bool = False,
    timeout_seconds: float = 180,
) -> dict[str, object]:
    from thoth.adapters.models.codex_broker import broker_for_workspace
    from thoth.adapters.models.codex_profile import CodexProfileHold

    try:
        return broker_for_workspace(workspace).start_login(
            wait_for_completion=wait_for_completion, timeout_seconds=timeout_seconds
        )
    except CodexProfileHold as exc:
        raise LocalCredentialHold(str(exc)) from exc


def start_console_login(provider: str, *, open_browser: bool = False) -> dict[str, object]:
    import webbrowser

    spec = company(provider)
    if spec is None or not spec.get("console_url"):
        raise LocalCredentialHold("MODEL_ACCOUNT_LOGIN_REQUIRED")
    if open_browser:
        webbrowser.open(spec["console_url"])
    return {
        "started": False,
        "provider": spec["provider"],
        "kind": "unsupported",
        "reason_code": "MODEL_ACCOUNT_LOGIN_UNSUPPORTED",
        "browser_url": spec["console_url"],
    }


def register_thoth_local_providers(
    resolver: RegisteredModelResolver, root: Path | None = None
) -> None:
    for item in available_credentials(root):
        provider = item["provider"]
        if resolver.contains(provider):
            continue
        resolver.register(
            provider,
            lambda model, bound=provider: thoth_local_model(bound, model, root),
        )
    resolver.register_dynamic(
        lambda provider: any(item["provider"] == provider for item in available_credentials(root)),
        lambda provider, model: thoth_local_model(provider, model, root),
    )


class LocalModelCredentials(ModelCredentialPort):
    def __init__(
        self, root: Path | None = None, *, auth_registry: AuthRegistry | None = None
    ) -> None:
        self.root = root
        self.auth_registry = auth_registry or default_auth_registry()

    @staticmethod
    def _method(provider: str, auth_method: str | None) -> str:
        if auth_method:
            return auth_method
        return {"openai": "codex_isolated_browser", "xai": "xai_device_code"}.get(provider, "")

    def _route(self, provider: str, auth_method: str | None) -> AuthRoute:
        if self.root is None:
            raise ModelCredentialError("MODEL_AUTH_WORKSPACE_REQUIRED")
        return self.auth_registry.resolve(provider, self._method(provider, auth_method))

    @staticmethod
    def _auth_error(exc: ModelExecutionHold) -> ModelCredentialError:
        reason = str(exc)
        return ModelCredentialError(
            reason
            if reason.isascii() and reason.replace("_", "").isalnum() and reason.upper() == reason
            else "MODEL_AUTH_FAILURE"
        )

    @staticmethod
    def _decorate(route: AuthRoute, result: dict[str, object]) -> dict[str, object]:
        capabilities = result.get("capabilities")
        if not isinstance(capabilities, dict):
            capabilities = {
                "start": True,
                "status": True,
                "cancel": True,
                "manual_complete": route.manual_complete,
            }
        return {
            **result,
            "account_provider": route.account_provider,
            "auth_method": route.auth_method,
            "route": route.model_route,
            "capabilities": capabilities,
        }

    def account_connections(self) -> list[dict[str, object]]:
        from thoth.adapters.models.codex_broker import broker_for_workspace as codex_broker

        status: dict[str, object]
        try:
            status = (
                codex_broker(self.root).local_status().public()
                if self.root is not None
                else {"connected": False, "reason_code": "CODEX_WORKSPACE_REQUIRED"}
            )
        except (OSError, subprocess.TimeoutExpired):
            status = {"connected": False, "reason_code": "CODEX_STATUS_UNAVAILABLE"}
        xai_status: dict[str, object] = {}
        claude_status: dict[str, object] = {}
        if self.root is not None:
            from thoth.adapters.models.xai_broker import broker_for_workspace

            xai_status = broker_for_workspace(self.root).status()
            from thoth.adapters.models.claude_oauth import broker_for_workspace as claude_broker

            claude_status = claude_broker(self.root).status()
        return account_connections(status, self.root, xai_status, claude_status)

    def list_credentials(self) -> tuple[dict[str, str], ...]:
        return available_credentials(self.root)

    def register_api_key(
        self,
        *,
        provider: str,
        model: str,
        api_key: str,
        base_url: str,
    ) -> dict[str, str]:
        try:
            return register_api_key(
                provider=provider,
                model=model,
                api_key=api_key,
                base_url=base_url,
                root=self.root,
            )
        except LocalCredentialHold as exc:
            raise ModelCredentialError(str(exc)) from exc

    def start_login(self, provider: str, auth_method: str | None = None) -> dict[str, object]:
        spec = company(provider)
        if spec is None:
            raise ModelCredentialError("MODEL_ACCOUNT_LOGIN_UNSUPPORTED")
        if provider == "anthropic" and auth_method is None:
            from thoth.adapters.models.claude_code import claude_code_login_guidance

            if self.root is None:
                raise ModelCredentialError("CLAUDE_CODE_WORKSPACE_REQUIRED")
            return claude_code_login_guidance(self.root)
        route = self._route(provider, auth_method)
        try:
            return self._decorate(route, route.factory(self.root).start_login())  # type: ignore[arg-type]
        except ModelExecutionHold as exc:
            raise self._auth_error(exc) from exc

    def login_status(
        self, provider: str, login_id: str | None = None, auth_method: str | None = None
    ) -> dict[str, object]:
        route = self._route(provider, auth_method)
        try:
            return self._decorate(route, route.factory(self.root).login_status(login_id))  # type: ignore[arg-type]
        except ModelExecutionHold as exc:
            raise self._auth_error(exc) from exc

    def cancel_login(
        self, provider: str, login_id: str, auth_method: str | None = None
    ) -> dict[str, object]:
        route = self._route(provider, auth_method)
        try:
            return self._decorate(route, route.factory(self.root).cancel_login(login_id))  # type: ignore[arg-type]
        except ModelExecutionHold as exc:
            raise self._auth_error(exc) from exc

    def submit_login_response(
        self, provider: str, login_id: str, response: str, auth_method: str
    ) -> dict[str, object]:
        route = self._route(provider, auth_method)
        if not route.manual_complete:
            raise ModelCredentialError("MODEL_LOGIN_COMPLETE_UNSUPPORTED")
        handler = route.factory(self.root)  # type: ignore[arg-type]
        submit = getattr(handler, "submit_login_response", None)
        if not callable(submit):
            raise ModelCredentialError("MODEL_LOGIN_COMPLETE_UNSUPPORTED")
        try:
            result: object = submit(login_id, response)
            if not isinstance(result, dict):
                raise ModelCredentialError("MODEL_AUTH_RESPONSE_INVALID")
            return self._decorate(route, cast(dict[str, object], result))
        except ModelExecutionHold as exc:
            raise self._auth_error(exc) from None
