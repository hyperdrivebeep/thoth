from collections.abc import Callable

from pydantic import JsonValue

from thoth.domain.base import DomainModel
from thoth.domain.deployment_mode import DeploymentMode, parse_deployment_mode
from thoth.domain.workspace_setup import WorkspaceSetupState
from thoth.ports.workspace_setup import WorkspaceSetupPort
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode


class WorkspaceSetupUpdateInput(DomainModel):
    internet_consent: str
    expected_revision: int | None = None


class WorkspaceSetupHandlers:
    def __init__(
        self,
        setup: WorkspaceSetupPort,
        *,
        deployment_mode: DeploymentMode | None = None,
        model_connected: Callable[[], bool],
        ready_projection: Callable[[WorkspaceSetupState], dict[str, JsonValue]] | None = None,
    ) -> None:
        self._setup = setup
        self._mode = deployment_mode or parse_deployment_mode()
        self._model_connected = model_connected
        self._ready_projection = ready_projection

    async def read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        del value
        setup = self._setup.read()
        if self._ready_projection is not None:
            projection = self._ready_projection(setup)
            payload = dict(setup.model_dump(mode="json"))
            for key in (
                "deployment_mode",
                "disclosure",
                "hosted_model",
                "credential_mode",
                "model_connected",
            ):
                if key in projection:
                    payload[key] = projection[key]
            return payload
        payload = setup.model_dump(mode="json")
        if self._mode is DeploymentMode.HOSTED_REVIEW:
            payload["deployment_mode"] = self._mode.value
        return payload

    async def update(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = WorkspaceSetupUpdateInput.model_validate(value)
        current = self._setup.read()
        if request.internet_consent not in {"UNDECIDED", "DENIED", "ALLOWED"}:
            raise RpcApplicationError(RpcErrorCode.INVALID_PARAMS, "INTERNET_CONSENT_INVALID")
        if request.expected_revision is not None and current.revision != request.expected_revision:
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED,
                "WORKSPACE_SETUP_REVISION_CONFLICT",
                data={"reason_code": "WORKSPACE_SETUP_REVISION_CONFLICT", "pre_io": True},
            )
        updated = self._setup.write(
            current.model_copy(update={"internet_consent": request.internet_consent})
        )
        payload = updated.model_dump(mode="json")
        if self._mode is DeploymentMode.HOSTED_REVIEW:
            payload["deployment_mode"] = self._mode.value
        return payload

    async def ready(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        del value
        setup = self._setup.read()
        if self._ready_projection is not None:
            return self._ready_projection(setup)
        accounts = self._model_connected()
        ready = setup.internet_consent != "UNDECIDED" and accounts
        return {
            "ready": ready,
            "setup": setup.model_dump(mode="json"),
            "model_connected": accounts,
        }
