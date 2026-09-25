"""Read/update local research preferences through the normal command bus."""

from collections.abc import Callable

from pydantic import JsonValue, TypeAdapter

from thoth.application.services.model_settings import ModelSettingsService
from thoth.domain.auth import current_authenticated_actor
from thoth.domain.base import DomainModel
from thoth.domain.model_settings import ModelSelection
from thoth.ports.model import ModelResolutionError
from thoth.ports.model_credentials import ModelCredentialError, ModelCredentialPort
from thoth.ports.project import ProjectStorePort
from thoth.ports.thread import ThreadStorePort
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode

_JSON_RESULT = TypeAdapter(dict[str, JsonValue])
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
    model: str = ""
    api_key: str = ""
    base_url: str = ""


class ModelSettingsHandlers:
    def __init__(
        self,
        service: ModelSettingsService,
        projects: ProjectStorePort,
        authorize: Callable[[str, dict[str, JsonValue]], None],
        threads: ThreadStorePort,
        credentials: ModelCredentialPort,
        unregistered_default_is_available: bool = True,
    ) -> None:
        self.service, self.projects, self.authorize = service, projects, authorize
        self.threads = threads
        self.credentials = credentials
        self.unregistered_default_is_available = unregistered_default_is_available

    def authorize_before_claim(self, method: str, value: dict[str, JsonValue]) -> None:
        if method.startswith("model/credential/"):
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

    async def read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        self.authorize_before_claim("model/settings/read", value)
        request = SettingsInput.model_validate(value)
        digest, selection = self.service.preference(request.project_id, request.thread_id)
        options = [option.model_dump(mode="json") for option in self.service.catalog.options()]
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
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc
        return await self.read({"project_id": request.project_id, "thread_id": request.thread_id})

    async def list_credentials(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        self.authorize_before_claim("model/credential/list", value)

        return _JSON_RESULT.validate_python(
            {
                "accounts": self.credentials.account_connections(),
                "credentials": [dict(item) for item in self.credentials.list_credentials()],
            }
        )

    async def register_credential(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        self.authorize_before_claim("model/credential/register", value)
        request = CredentialInput.model_validate(value)

        if request.api_key.strip():
            try:
                recorded = self.credentials.register_api_key(
                    provider=request.provider,
                    model=request.model,
                    api_key=request.api_key,
                    base_url=request.base_url,
                )
            except ModelCredentialError as exc:
                raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc
            return _JSON_RESULT.validate_python({"credential": recorded})
        try:
            return _JSON_RESULT.validate_python(self.credentials.start_login(request.provider))
        except ModelCredentialError as exc:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc
