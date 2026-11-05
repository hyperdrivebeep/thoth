"""project/permanentDelete: remove an archived project and everything stored under it.

The one exception to the immutable ledger rule is this command: the user names an archived
project and types its exact name. Archive stays the reversible step; nothing else deletes.
"""

from __future__ import annotations

from collections.abc import Callable

from pydantic import Field, JsonValue

from thoth.application.services.actor_authority import current_actor_for
from thoth.domain.base import DomainModel
from thoth.domain.deployment_mode import DeploymentMode
from thoth.domain.enums import ProjectLifecycle
from thoth.domain.project_deletion import ProjectDeletionRecord, project_name_digest
from thoth.domain.resource_scope import ResourceScopeError
from thoth.ports.auth import AuthSessionStorePort
from thoth.ports.governance import GovernanceStorePort
from thoth.ports.ledger import LedgerPort
from thoth.ports.project import ProjectStorePort
from thoth.ports.project_purge import ProjectPurgeBlocked, ProjectPurgePort
from thoth.ports.runtime import ClockPort
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode

PERMANENT_DELETE_METHOD = "project/permanentDelete"


class ProjectPermanentDeleteInput(DomainModel):
    project_id: str = Field(min_length=1, max_length=160)
    confirm_name: str = Field(min_length=1, max_length=500)


def _rejected(reason: str, **data: JsonValue) -> RpcApplicationError:
    return RpcApplicationError(
        RpcErrorCode.DOMAIN_REJECTED,
        "project could not be permanently deleted",
        data={"reason_code": reason, **data},
    )


def _denied(reason: str) -> RpcApplicationError:
    return RpcApplicationError(
        RpcErrorCode.AUTHORIZATION_DENIED,
        "project permanent deletion is not allowed",
        data={"reason_code": reason, "pre_io": True},
    )


class ProjectDeletionHandlers:
    def __init__(
        self,
        *,
        store: ProjectStorePort,
        purge: ProjectPurgePort,
        ledger: LedgerPort,
        sessions: AuthSessionStorePort,
        governance: GovernanceStorePort,
        clock: ClockPort,
        mode: DeploymentMode,
    ) -> None:
        self._store = store
        self._purge = purge
        self._ledger = ledger
        self._sessions = sessions
        self._governance = governance
        self._clock = clock
        self._mode = mode
        self._seal_dead_operations: Callable[[str], int] | None = None

    def bind_dead_owner_sealer(self, seal: Callable[[str], int]) -> None:
        """Let the runtime seal this project's operations whose owner process is confirmed gone."""
        self._seal_dead_operations = seal

    def _seal_before_delete(self, request: ProjectPermanentDeleteInput) -> None:
        """Make leftovers of a killed process stop counting as active work for this delete.

        Only a project that this request is about to delete is touched, and only after the same
        checks the delete itself makes first. The sealing is its own committed change: if the
        delete is then refused, an operation whose owner is gone stays sealed.
        """
        if self._seal_dead_operations is None:
            return
        project = self._store.read(request.project_id)
        if (
            project is not None
            and project.lifecycle == ProjectLifecycle.ARCHIVED_READ_ONLY
            and request.confirm_name == project.name
        ):
            self._seal_dead_operations(request.project_id)

    def authorize_before_claim(self, method: str, value: dict[str, JsonValue]) -> None:
        """Runs before the operation is claimed, so a refusal leaves no record at all."""
        del method  # the bus passes it to every pre-claim authorizer; this one owns one method
        project_id = str(value.get("project_id", ""))
        try:
            actor = current_actor_for(project_id, self._sessions, self._governance, self._clock)
        except (ResourceScopeError, PermissionError) as exc:
            raise _denied("PROJECT_DELETE_SCOPE_DENIED") from exc
        if actor is not None and (
            not {"WRITE", "ADMIN"}.issubset(actor.capabilities)
            or "PROJECT" not in actor.data_scopes
        ):
            raise _denied("PROJECT_DELETE_SCOPE_DENIED")
        if self._mode is not DeploymentMode.LOCAL:
            raise _denied("PROJECT_DELETE_LOCAL_ONLY")

    async def permanent_delete(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        self.authorize_before_claim(PERMANENT_DELETE_METHOD, value)
        request = ProjectPermanentDeleteInput.model_validate(value)
        digest = project_name_digest(request.confirm_name)
        deleted_rows = 0
        self._seal_before_delete(request)
        with self._ledger.transaction():
            record = self._purge.read_record(request.project_id)
            already = record is not None
            if record is None:
                record, deleted_rows = self._delete(request, digest)
            elif record.name_sha256 != digest:
                raise RpcApplicationError(
                    RpcErrorCode.PROJECT_NOT_FOUND,
                    "project was not found",
                    data={"project_id": request.project_id},
                )
        remaining = self._purge.clean_files(request.project_id)
        result: dict[str, JsonValue] = {
            "project_id": request.project_id,
            "deleted": True,
            "already_deleted": already,
            "deleted_at": record.deleted_at.isoformat(),
            "deleted_rows": deleted_rows,
            "cleanup_pending": list(remaining),
        }
        if already:
            result["unchanged"] = True
        return result

    def _delete(
        self, request: ProjectPermanentDeleteInput, digest: str
    ) -> tuple[ProjectDeletionRecord, int]:
        project = self._store.read(request.project_id)
        if project is None:
            raise RpcApplicationError(
                RpcErrorCode.PROJECT_NOT_FOUND,
                "project was not found",
                data={"project_id": request.project_id},
            )
        if project.lifecycle != ProjectLifecycle.ARCHIVED_READ_ONLY:
            raise _rejected("PROJECT_NOT_ARCHIVED")
        if request.confirm_name != project.name:
            raise _rejected("PROJECT_NAME_MISMATCH")
        try:
            result = self._purge.purge(
                ProjectDeletionRecord(
                    project_id=project.project_id,
                    name_sha256=digest,
                    deleted_at=self._clock.now(),
                )
            )
        except ProjectPurgeBlocked as exc:
            data: dict[str, JsonValue] = {}
            if exc.project_ids:
                data["referencing_project_ids"] = list(exc.project_ids)
            raise _rejected(exc.reason_code, **data) from exc
        return result.record, result.deleted_rows
