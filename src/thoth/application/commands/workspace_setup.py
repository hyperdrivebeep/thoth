import asyncio
from collections.abc import Callable

from pydantic import JsonValue

from thoth.domain.base import DomainModel
from thoth.domain.deployment_mode import DeploymentMode, parse_deployment_mode
from thoth.domain.workspace_setup import LocalWorkspaceReady, WorkspaceSetupState
from thoth.ports.workspace_setup import WorkspaceSetupPort, WorkspaceSetupUnavailable
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
        workspace_id: str | None = None,
    ) -> None:
        self._setup = setup
        self._mode = deployment_mode or parse_deployment_mode()
        self._model_connected = model_connected
        self._ready_projection = ready_projection
        self._workspace_id = workspace_id

    def _setup_unavailable(
        self, error: WorkspaceSetupUnavailable, *, readiness: bool
    ) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {
            "setup_status": error.setup_status,
            "reason_code": error.reason_code,
            "workspace_readable": True,
            "execution_ready": False,
        }
        if readiness:
            payload.update(
                {
                    "ready": False,
                    "setup": None,
                    "setup_complete": False,
                    "model_connected": False,
                }
            )
        if self._workspace_id is not None:
            payload["workspace_id"] = self._workspace_id
        if self._mode is DeploymentMode.HOSTED_REVIEW:
            payload["deployment_mode"] = self._mode.value
        return payload

    @staticmethod
    def _read_error(setup: WorkspaceSetupState) -> WorkspaceSetupUnavailable | None:
        if setup.storage_status == "CORRUPT":
            return WorkspaceSetupUnavailable("WORKSPACE_SETUP_CORRUPT")
        if setup.storage_status == "UNREADABLE":
            return WorkspaceSetupUnavailable("WORKSPACE_SETUP_UNREADABLE")
        return None

    async def read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        del value
        try:
            setup = self._setup.read()
        except WorkspaceSetupUnavailable as exc:
            return self._setup_unavailable(exc, readiness=False)
        if (error := self._read_error(setup)) is not None:
            return self._setup_unavailable(error, readiness=False)
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
        try:
            current = self._setup.read()
        except WorkspaceSetupUnavailable as exc:
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED,
                exc.reason_code,
                data={"reason_code": exc.reason_code, "pre_io": True},
            ) from None
        if (error := self._read_error(current)) is not None:
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED,
                error.reason_code,
                data={"reason_code": error.reason_code, "pre_io": True},
            ) from None
        if request.internet_consent not in {"UNDECIDED", "DENIED", "ALLOWED"}:
            raise RpcApplicationError(RpcErrorCode.INVALID_PARAMS, "INTERNET_CONSENT_INVALID")
        if request.expected_revision is not None and current.revision != request.expected_revision:
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED,
                "WORKSPACE_SETUP_REVISION_CONFLICT",
                data={"reason_code": "WORKSPACE_SETUP_REVISION_CONFLICT", "pre_io": True},
            )
        try:
            updated = self._setup.write(
                current.model_copy(update={"internet_consent": request.internet_consent})
            )
        except WorkspaceSetupUnavailable as exc:
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED,
                exc.reason_code,
                data={"reason_code": exc.reason_code, "pre_io": True},
            ) from None
        except OSError:
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED, "WORKSPACE_SETUP_WRITE_FAILED"
            ) from None
        payload = updated.model_dump(mode="json")
        if self._mode is DeploymentMode.HOSTED_REVIEW:
            payload["deployment_mode"] = self._mode.value
        return payload

    async def ready(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        del value
        try:
            setup = self._setup.read()
        except WorkspaceSetupUnavailable as exc:
            return self._setup_unavailable(exc, readiness=True)
        if (error := self._read_error(setup)) is not None:
            return self._setup_unavailable(error, readiness=True)
        if self._ready_projection is not None:
            return self._ready_projection(setup)
        accounts = await asyncio.to_thread(self._model_connected)
        ready = setup.internet_consent != "UNDECIDED" and accounts
        payload: dict[str, JsonValue] = {
            "ready": ready,
            "setup": setup.model_dump(mode="json"),
            "model_connected": accounts,
        }
        if self._mode is DeploymentMode.LOCAL:
            if self._workspace_id is None:
                # Legacy direct constructors retain their old projection. The normal
                # composition always supplies an ID; the UI never persists on a missing ID.
                return payload
            return LocalWorkspaceReady(
                ready=ready,
                setup=setup,
                model_connected=accounts,
                setup_complete=setup.internet_consent != "UNDECIDED",
                workspace_readable=True,
                execution_ready=ready,
                workspace_id=self._workspace_id,
            ).model_dump(mode="json")
        return payload
