"""THOTH-owned model credentials. Secrets stay in the workspace file, never in RPC results."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from contextlib import suppress
from pathlib import Path
from typing import cast

from openai import AsyncOpenAI

from thoth.adapters.models.anthropic_messages import AnthropicMessagesModel
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
    codex_status: dict[str, object], root: Path | None = None
) -> list[dict[str, object]]:
    listed = {item["provider"] for item in available_credentials(root)}
    rows: list[dict[str, object]] = []
    for key, spec in COMPANIES.items():
        oauth = key == "openai" and codex_status.get("connected") is True
        has_key = spec["provider"] in listed
        model_providers = [spec["provider"]] if has_key else []
        if oauth:
            model_providers.append("codex-oauth")
        rows.append(
            {
                "provider": spec["provider"],
                "label": spec["label"],
                "kind": "account",
                "connected": oauth or has_key,
                "has_key": has_key,
                "oauth": oauth,
                "remote_auth_verified": None,
                "login_supported": key == "openai",
                "login_kind": "codex_device_auth" if key == "openai" else "unsupported",
                "available_model_providers": model_providers,
            }
        )
    return rows


def start_codex_login() -> dict[str, object]:
    from thoth.adapters.models.codex_oauth import (
        CodexOAuthUnavailable,
        resolve_codex_executable,
    )

    try:
        resolved = resolve_codex_executable()
        if not resolved.is_file():
            raise LocalCredentialHold("CODEX_CLI_NOT_FOUND")
        creationflags = (
            getattr(subprocess, "CREATE_NEW_CONSOLE", 0) if sys.platform == "win32" else 0
        )
        if creationflags:
            subprocess.Popen(
                [str(resolved), "login", "--device-auth"],
                creationflags=creationflags,
            )
            return {"started": True, "provider": "codex-oauth", "kind": "oauth"}
        # Headless/device login waits for a person to finish a browser step.
        # Never hold the HTTP event loop (or hide the one-time code in server logs).
        return {
            "started": False,
            "provider": "codex-oauth",
            "kind": "manual_device_auth",
            "reason_code": "CODEX_DEVICE_AUTH_TERMINAL_REQUIRED",
        }
    except CodexOAuthUnavailable as exc:
        raise LocalCredentialHold(str(exc)) from exc


def start_claude_login() -> dict[str, object]:
    executable = shutil.which("claude") or shutil.which("claude.exe")
    if executable:
        creationflags = (
            getattr(subprocess, "CREATE_NEW_CONSOLE", 0) if sys.platform == "win32" else 0
        )
        subprocess.Popen([executable, "login"], creationflags=creationflags)
        return {"started": True, "provider": "anthropic", "kind": "oauth"}
    return start_console_login("anthropic")


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
    def __init__(self, root: Path | None = None) -> None:
        self.root = root

    def account_connections(self) -> list[dict[str, object]]:
        from thoth.adapters.models.codex_oauth import codex_oauth_status

        status: dict[str, object]
        try:
            status = codex_oauth_status()
        except (OSError, subprocess.TimeoutExpired):
            status = {"connected": False}
        return account_connections(status, self.root)

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

    def start_login(self, provider: str) -> dict[str, object]:
        spec = company(provider)
        if spec is None:
            raise ModelCredentialError("MODEL_ACCOUNT_LOGIN_UNSUPPORTED")
        login = spec["login"]
        try:
            if login == "codex":
                return start_codex_login()
            return start_console_login(provider, open_browser=False)
        except LocalCredentialHold as exc:
            raise ModelCredentialError(str(exc)) from exc
