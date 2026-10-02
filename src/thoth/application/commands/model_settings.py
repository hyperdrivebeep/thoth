"""Read/update local research preferences through the normal command bus."""

import asyncio
import re
from collections.abc import Callable

from pydantic import JsonValue, TypeAdapter

from thoth.application.services.model_settings import ModelSettingsService
from thoth.domain.auth import current_authenticated_actor
from thoth.domain.base import DomainModel
from thoth.domain.model_settings import ModelSelection
from thoth.ports.model import ModelResolutionError
from thoth.ports.model_catalog import CatalogStatusPort
from thoth.ports.model_credentials import ModelCredentialError, ModelCredentialPort
from thoth.ports.model_tooling import ModelToolingError, ModelToolingPort
from thoth.ports.project import ProjectStorePort
from thoth.ports.thread import ThreadStorePort
from thoth.protocol.deferred import EphemeralCommandResult
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode

_JSON_RESULT = TypeAdapter(dict[str, JsonValue])
_TOOL_ID = re.compile(r"[a-z][a-z0-9-]{0,31}")
# npm may take up to 300 s; the adapter stops it and reports a typed reason first.
_TOOL_INSTALL_TIMEOUT_SECONDS = 330
_WORKSPACE_METHOD_PREFIXES = ("model/credential/", "model/catalog/", "model/tooling/")
_DURABLE_AUTH_FIELDS = frozenset(
    {
        "started",
        "provider",
        "account_provider",
        "auth_method",
        "route",
        "kind",
        "profile_id",
        "login_id",
        "state",
        "login_state",
        "auth_state",
        "catalog_state",
        "reason_code",
        "connected",
        "connection_state",
        "profile_mode",
        "execution_eligible",
        "execution_verified",
        "expires_at",
    }
)
_COMPLETE_ERROR_REASONS = frozenset(
    {
        "MODEL_ACCOUNT_LOGIN_UNSUPPORTED",
        "MODEL_AUTH_WORKSPACE_REQUIRED",
        "MODEL_LOGIN_COMPLETE_UNSUPPORTED",
        "MODEL_AUTH_RESPONSE_INVALID",
        "CLAUDE_CLIENT_REGISTRATION_REQUIRED",
        "CLAUDE_LOGIN_NOT_FOUND",
        "CLAUDE_LOGIN_NOT_PENDING",
        "CLAUDE_LOGIN_EXPIRED",
        "CLAUDE_LOGIN_CANCELLED",
        "CLAUDE_LOGIN_SUPERSEDED",
        "CLAUDE_LOGIN_RESPONSE_INVALID",
        "CLAUDE_TOKEN_EXCHANGE_PENDING",
        "CLAUDE_CODE_LOGIN_NOT_FOUND",
        "CLAUDE_CODE_LOGIN_NOT_PENDING",
        "CLAUDE_CODE_LOGIN_RESPONSE_INVALID",
        "CLAUDE_CODE_LOGIN_INPUT_UNAVAILABLE",
    }
)
_UNAVAILABLE_REASONS = frozenset(
    {
        "MODEL_ROUTE_INCOMPLETE",
        "MODEL_CAPABILITY_UNKNOWN",
        "MODEL_REASONING_EFFORT_UNSUPPORTED",
    }
)


class SettingsInput(DomainModel):
    project_id: str
    thread_id: str | None = None
    selection: ModelSelection = ModelSelection()
    expected_digest: str | None = None


class CredentialInput(DomainModel):
    project_id: str
    provider: str
    auth_method: str | None = None
    model: str = ""
    api_key: str = ""
    base_url: str = ""


class LoginInput(DomainModel):
    provider: str
    auth_method: str | None = None
    login_id: str | None = None


class ModelSettingsHandlers:
    def __init__(
        self,
        service: ModelSettingsService,
        projects: ProjectStorePort,
        authorize: Callable[[str, dict[str, JsonValue]], None],
        threads: ThreadStorePort,
        credentials: ModelCredentialPort,
        unregistered_default_is_available: bool = True,
        tooling: ModelToolingPort | None = None,
    ) -> None:
        self.service, self.projects, self.authorize = service, projects, authorize
        self.threads = threads
        self.credentials = credentials
        self.tooling = tooling
        self.unregistered_default_is_available = unregistered_default_is_available

    def authorize_before_claim(self, method: str, value: dict[str, JsonValue]) -> None:
        if method.startswith(_WORKSPACE_METHOD_PREFIXES):
            return
        self.authorize(method, value)
        if self.projects.read(str(value.get("project_id", ""))) is None:
            raise RpcApplicationError(RpcErrorCode.PROJECT_NOT_FOUND, "project not found")
        if value.get("thread_id"):
            thread = self.threads.read(str(value["thread_id"]))
            if thread is None or thread.project_id != value["project_id"]:
                raise RpcApplicationError(
                    RpcErrorCode.THREAD_NOT_FOUND, "thread not found in project"
                )
        actor = current_authenticated_actor()
        if actor is not None and not value.get("thread_id") and "PROJECT" not in actor.data_scopes:
            raise RpcApplicationError(
                RpcErrorCode.AUTHORIZATION_DENIED, "PROJECT_SETTINGS_SCOPE_REQUIRED"
            )

    async def _refresh_catalog(self) -> None:
        refresh = getattr(self.service.catalog, "refresh", None)
        if callable(refresh):
            try:
                await asyncio.wait_for(asyncio.to_thread(refresh), timeout=20)
            except TimeoutError as exc:
                raise RpcApplicationError(
                    RpcErrorCode.DOMAIN_REJECTED, "MODEL_CATALOG_REFRESH_TIMEOUT"
                ) from exc

    def _catalog_status(self) -> list[JsonValue]:
        """Where each list came from and how fresh it is, from what is already stored."""

        catalog = self.service.catalog
        rows = catalog.statuses() if isinstance(catalog, CatalogStatusPort) else ()
        return [row.model_dump(mode="json") for row in rows]

    def _status_of(self, provider: str | None) -> str:
        rows = self._catalog_status()
        found = next(
            (
                row
                for row in rows
                if isinstance(row, dict) and (provider is None or row.get("provider") == provider)
            ),
            None,
        )
        return str(found["status"]) if isinstance(found, dict) else "NONE"

    async def read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        """Settings come from the stored model list; a read never asks a provider for one."""

        self.authorize_before_claim("model/settings/read", value)
        request = SettingsInput.model_validate(value)
        digest, selection = self.service.preference(request.project_id, request.thread_id)
        options = [option.model_dump(mode="json") for option in self.service.catalog.options()]
        catalog_status = self._catalog_status()
        try:
            resolved = self.service.resolve(
                request.project_id, request.thread_id, request.selection
            )
        except ModelResolutionError as exc:
            reason = str(exc)
            return _JSON_RESULT.validate_python(
                {
                    "settings_digest": digest,
                    "selection": selection.model_dump(mode="json"),
                    "effective_settings": None,
                    "model_options": options,
                    "catalog_status": catalog_status,
                    "availability": "UNAVAILABLE",
                    "reason_code": (
                        reason if reason in _UNAVAILABLE_REASONS else "MODEL_SETTINGS_UNAVAILABLE"
                    ),
                }
            )
        if (
            resolved.capability_source == "UNREGISTERED_DEFAULT"
            and not self.unregistered_default_is_available
        ):
            return _JSON_RESULT.validate_python(
                {
                    "settings_digest": digest,
                    "selection": selection.model_dump(mode="json"),
                    "effective_settings": None,
                    "model_options": options,
                    "catalog_status": catalog_status,
                    "availability": "UNAVAILABLE",
                    "reason_code": "MODEL_CAPABILITY_UNKNOWN",
                }
            )
        return _JSON_RESULT.validate_python(
            {
                "settings_digest": digest,
                "selection": selection.model_dump(mode="json"),
                "effective_settings": resolved.model_dump(mode="json"),
                "model_options": options,
                "catalog_status": catalog_status,
                "availability": "AVAILABLE",
                "reason_code": None,
            },
        )

    async def update(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        self.authorize_before_claim("model/settings/update", value)
        request = SettingsInput.model_validate(value)
        actor = current_authenticated_actor()
        try:
            self.service.save(
                request.project_id,
                request.thread_id,
                request.selection,
                request.expected_digest,
                "human:local-user" if actor is None else actor.actor_id,
            )
        except ModelResolutionError as exc:
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED,
                str(exc),
                data={"catalog_status": self._status_of(request.selection.resolved_provider())},
            ) from exc
        return await self.read({"project_id": request.project_id, "thread_id": request.thread_id})

    async def list_credentials(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        self.authorize_before_claim("model/credential/list", value)
        accounts, credentials = await self._read_accounts()
        return _JSON_RESULT.validate_python({"accounts": accounts, "credentials": credentials})

    async def _read_accounts(self) -> tuple[list[JsonValue], list[JsonValue]]:
        try:
            accounts = await asyncio.wait_for(
                asyncio.to_thread(self.credentials.account_connections), timeout=20
            )
            credentials = await asyncio.wait_for(
                asyncio.to_thread(self.credentials.list_credentials), timeout=5
            )
        except TimeoutError as exc:
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED, "MODEL_CREDENTIAL_STATUS_TIMEOUT"
            ) from exc
        return (
            TypeAdapter(list[JsonValue]).validate_python(accounts),
            TypeAdapter(list[JsonValue]).validate_python([dict(item) for item in credentials]),
        )

    async def install_tool(self, value: dict[str, JsonValue]) -> EphemeralCommandResult:
        """Install one named helper tool, then return the refreshed account rows."""
        self.authorize_before_claim("model/tooling/install", value)
        tool_id = value.get("tool_id")
        if not isinstance(tool_id, str) or _TOOL_ID.fullmatch(tool_id) is None:
            raise RpcApplicationError(RpcErrorCode.INVALID_PARAMS, "MODEL_TOOL_ID_INVALID")
        if self.tooling is None:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "MODEL_TOOLING_UNAVAILABLE")
        try:
            outcome = await asyncio.wait_for(
                asyncio.to_thread(self.tooling.install, tool_id),
                timeout=_TOOL_INSTALL_TIMEOUT_SECONDS,
            )
        except ModelToolingError as exc:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc
        except TimeoutError as exc:
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED, "MODEL_TOOL_INSTALL_TIMEOUT"
            ) from exc
        accounts, _credentials = await self._read_accounts()
        summary: dict[str, JsonValue] = {
            "tool_id": tool_id,
            "installed": outcome.get("installed") is True,
            "version": str(outcome.get("version") or ""),
        }
        return EphemeralCommandResult(
            response_value=_JSON_RESULT.validate_python({**summary, "accounts": accounts}),
            durable_value=summary,
        )

    async def refresh_catalog(self, value: dict[str, JsonValue]) -> EphemeralCommandResult:
        """Fetch the provider model lists once, on an explicit request.

        Login completion and the "load models" button call this. Status polling never does,
        so a status read cannot start remote catalog traffic.
        """
        self.authorize_before_claim("model/catalog/refresh", value)
        await self._refresh_catalog()
        accounts, credentials = await self._read_accounts()
        count = len(self.service.catalog.options())
        return EphemeralCommandResult(
            response_value=_JSON_RESULT.validate_python(
                {
                    "accounts": accounts,
                    "credentials": credentials,
                    "model_option_count": count,
                    "catalog_status": self._catalog_status(),
                    "catalog_refreshed": True,
                }
            ),
            durable_value={"catalog_refreshed": True, "model_option_count": count},
        )

    @staticmethod
    def _ephemeral_auth_result(result: dict[str, JsonValue]) -> EphemeralCommandResult:
        durable = {key: child for key, child in result.items() if key in _DURABLE_AUTH_FIELDS}
        return EphemeralCommandResult(response_value=result, durable_value=durable)

    async def register_credential(
        self, value: dict[str, JsonValue]
    ) -> dict[str, JsonValue] | EphemeralCommandResult:
        self.authorize_before_claim("model/credential/register", value)
        request = CredentialInput.model_validate(value)

        if request.api_key.strip():
            try:
                recorded = await asyncio.wait_for(
                    asyncio.to_thread(
                        self.credentials.register_api_key,
                        provider=request.provider,
                        model=request.model,
                        api_key=request.api_key,
                        base_url=request.base_url,
                    ),
                    timeout=20,
                )
            except ModelCredentialError as exc:
                raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc
            return _JSON_RESULT.validate_python({"credential": recorded})
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    self.credentials.start_login, request.provider, request.auth_method
                ),
                timeout=20,
            )
            return self._ephemeral_auth_result(_JSON_RESULT.validate_python(result))
        except ModelCredentialError as exc:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc

    async def login_status(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        self.authorize_before_claim("model/credential/login/status", value)
        request = LoginInput.model_validate(value)
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    self.credentials.login_status,
                    request.provider,
                    request.login_id,
                    request.auth_method,
                ),
                timeout=5,
            )
            return _JSON_RESULT.validate_python(result)
        except (ModelCredentialError, TimeoutError) as exc:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc

    async def cancel_login(
        self, value: dict[str, JsonValue]
    ) -> dict[str, JsonValue] | EphemeralCommandResult:
        self.authorize_before_claim("model/credential/login/cancel", value)
        request = LoginInput.model_validate(value)
        if request.login_id is None:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "MODEL_LOGIN_ID_REQUIRED")
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    self.credentials.cancel_login,
                    request.provider,
                    request.login_id,
                    request.auth_method,
                ),
                timeout=5,
            )
            return self._ephemeral_auth_result(_JSON_RESULT.validate_python(result))
        except (ModelCredentialError, TimeoutError) as exc:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc

    async def complete_login(self, value: dict[str, JsonValue]) -> EphemeralCommandResult:
        """Accept a manual callback without validating or persisting its secret as an error."""
        self.authorize_before_claim("model/credential/login/complete", value)
        provider = value.get("provider")
        auth_method = value.get("auth_method")
        login_id = value.get("login_id")
        response = value.get("response")
        if (
            not isinstance(provider, str)
            or not provider
            or not isinstance(auth_method, str)
            or not auth_method
            or not isinstance(login_id, str)
            or not login_id
            or not isinstance(response, str)
            or not response
            or len(response) > 8192
        ):
            raise RpcApplicationError(RpcErrorCode.INVALID_PARAMS, "MODEL_LOGIN_COMPLETE_INVALID")
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    self.credentials.submit_login_response,
                    provider,
                    login_id,
                    response,
                    auth_method,
                ),
                timeout=5,
            )
            validated = _JSON_RESULT.validate_python(result)
            safe = {
                key: child
                for key, child in validated.items()
                if key in _DURABLE_AUTH_FIELDS
                or key in {"capabilities", "manual_response_required", "cleanup_confirmed"}
            }
            return EphemeralCommandResult(response_value=safe, durable_value=safe)
        except TimeoutError as exc:
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED, "MODEL_LOGIN_COMPLETE_TIMEOUT"
            ) from exc
        except ModelCredentialError as exc:
            reason = str(exc)
            safe_reason = (
                reason if reason in _COMPLETE_ERROR_REASONS else "MODEL_LOGIN_COMPLETE_FAILED"
            )
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, safe_reason) from None
