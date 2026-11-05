"""LOCAL recovery for measured commands whose original worker is confirmed gone."""

import logging
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Literal

from thoth.domain.deployment_mode import STALE_RUNNING_MESSAGE, STALE_RUNNING_REASON
from thoth.domain.enums import OperationState
from thoth.domain.operation import OperationOwnerRecord, OperationRecord
from thoth.domain.resource_scope import current_resource_uses, resource_use_scope
from thoth.ports.journal import JournalPort
from thoth.ports.operation import OperationStorePort
from thoth.ports.resource_scope import ResourceAccessPort
from thoth.ports.runtime import AtomicUnitOfWorkPort, ClockPort
from thoth.ports.worker_identity import WorkerIdentityPort
from thoth.protocol.jsonrpc import JsonRpcError, JsonRpcRequest, RpcErrorCode

_LOG = logging.getLogger("thoth.operation_owner")
OwnerVerdict = Literal["GONE", "ALIVE", "UNPROVEN", "NO_OWNER"]

# V-S2 approved evidence is pinned by tests/fixtures/sealable_methods.json.
SEALABLE_METHODS: frozenset[str] = frozenset(
    {
        "action/authorization/decide",
        "action/plan/revise",
        "action/revise",
        "action/select",
        "action/step/add",
        "action/step/revise",
        "closure/decide",
        "closure/finalize",
        "closure/reopen",
        "criteria/field/correct",
        "criteria/profile/apply",
        "execution/cancel",
        "execution/observation/link",
        "execution/pause",
        "execution/resume",
        "execution/retry",
        "execution/start",
        "hypothesis/assumption/add",
        "hypothesis/causal/update",
        "hypothesis/intent/update",
        "hypothesis/prediction/bind",
        "hypothesis/relation/add",
        "hypothesis/relation/remove",
        "hypothesis/revise",
        "improvement/evaluation/assess",
        "improvement/exposure/complete",
        "improvement/exposure/decide",
        "improvement/exposure/rollback",
        "improvement/exposure/start",
        "improvement/promotion/decide",
        "improvement/revise",
        "investigation/pause",
        "investigation/resume",
        "investigation/stop",
        "investigation/update",
        "memory/candidate/classify",
        "memory/settings/update",
        "model/callSettings/update",
        "model/settings/update",
        "object/attention/acknowledge",
        "object/facet/update",
        "object/frame/revise",
        "object/profile/apply",
        "object/relation/add",
        "object/relation/remove",
        "outcome/assess",
        "outcome/observation/link",
        "outcome/reassess",
        "project/activate",
        "project/cutoff/update",
        "project/metadata/update",
        "project/overlay/update",
        "project/policy/update",
        "project/reference/import",
        "project/role/assign",
        "project/role/revoke",
        "project/source/scope/assign",
        "project/source/scope/grant",
        "project/source/scope/revoke",
        "project/source/scope/update",
        "project/source/time/confirm",
        "project/source/time/correct",
        "revision/baseline/decide",
        "revision/changeSet/validate",
        "revision/merge/resolve",
        "thread/metadata/update",
        "trace/confirm",
    }
)


def owner_gone_error() -> JsonRpcError:
    return JsonRpcError(
        code=RpcErrorCode.DOMAIN_REJECTED,
        message=(
            "이전에 보낸 같은 요청을 실행하던 THOTH가 끝나서 요청이 마무리되지 못했습니다. "
            "변경이 저장됐는지 확인한 뒤 필요하면 새 요청으로 다시 보내세요. / "
            "The THOTH process that was running this request ended before it finished. "
            "Check whether the change was saved, then send it again as a new request "
            "if you still need it."
        ),
        data={
            "reason_code": "OPERATION_OWNER_GONE",
            "remote_observation": "UNKNOWN",
            "new_request_required": True,
        },
    )


def stale_running_error() -> JsonRpcError:
    return JsonRpcError(
        code=RpcErrorCode.DOMAIN_REJECTED,
        message=STALE_RUNNING_MESSAGE,
        data={"reason_code": STALE_RUNNING_REASON, "remote_observation": "UNKNOWN"},
    )


class OperationOwner:
    """Injected LOCAL policy; OS identity/liveness is never read inside the UoW."""

    def __init__(
        self,
        *,
        worker: WorkerIdentityPort,
        host_token: str | None,
        holds_workspace: Callable[[], bool],
        pin_workspace: Callable[[], AbstractContextManager[bool]],
        operations: OperationStorePort,
        uow: AtomicUnitOfWorkPort,
        journal: JournalPort,
        clock: ClockPort,
        resource_access: ResourceAccessPort | None = None,
    ) -> None:
        self._worker = worker
        self._host_token = host_token
        self._holds_workspace = holds_workspace
        self._pin_workspace = pin_workspace
        self._operations = operations
        self._uow = uow
        self._journal = journal
        self._clock = clock
        self._resource_access = resource_access
        self._record = (
            None
            if host_token is None or worker.process_marker == "UNKNOWN"
            else OperationOwnerRecord(
                worker_id=worker.worker_id,
                process_id=worker.process_id,
                process_marker=worker.process_marker,
                host_token=host_token,
            )
        )

    def record(self) -> OperationOwnerRecord | None:
        return self._record

    def verdict(self, operation: OperationRecord) -> OwnerVerdict:
        """GONE only when the owner is confirmed not running; ALIVE when confirmed running."""
        owner = operation.owner
        if operation.state is not OperationState.RUNNING:
            return "UNPROVEN"
        if owner is None:
            return "NO_OWNER"
        if (
            self._record is None
            or owner.host_token is None
            or owner.host_token != self._host_token
            or owner.process_marker == "UNKNOWN"
            or not self._holds_workspace()
        ):
            return "UNPROVEN"
        alive = self._worker.is_alive(owner.process_id, owner.process_marker, owner.worker_id)
        if alive is None:
            return "UNPROVEN"
        return "ALIVE" if alive else "GONE"

    def gone(self, operation: OperationRecord, method: str) -> bool:
        return (
            method == operation.method
            and method in SEALABLE_METHODS
            and self.verdict(operation) == "GONE"
        )

    def seal(
        self,
        request: JsonRpcRequest,
        operation: OperationRecord,
        measure: Callable[[JsonRpcRequest, str, str], None],
    ) -> OperationRecord | None:
        if request.params.meta.idempotency_key != operation.idempotency_key or not self.gone(
            operation, request.method
        ):
            return None
        found, sealed_now = self._fail_gone(operation, inside_request=False)
        if sealed_now and found is not None:
            measure(request, found.project_id, "FAILED")
        return found

    def _fail_gone(
        self, operation: OperationRecord, *, inside_request: bool
    ) -> tuple[OperationRecord | None, bool]:
        """Fail one operation whose owner is gone; the bool says this call changed it.

        Inside a request another project's scope is already open, so a nested scope for the
        operation's own project would be refused; the request's scope is reused then.
        """
        with self._pin_workspace() as held:
            if not held:
                return None, False
            scope = resource_use_scope(None if inside_request else operation.project_id)
            with scope, self._uow.transaction():
                current = self._operations.read(operation.operation_id)
                if current is None:
                    return None, False
                if self._resource_access is not None:
                    self._resource_access.require_operation(current)
                if current.state is not OperationState.RUNNING:
                    return current, False
                if current.owner != operation.owner:
                    return current, False
                error = owner_gone_error()
                payload = error.model_dump(mode="json")
                if self._resource_access is not None:
                    self._resource_access.require_operation(
                        current.model_copy(
                            update={"error": payload, "resource_uses": current_resource_uses()}
                        )
                    )
                failed = self._operations.fail(
                    current.operation_id, payload, completed_at=self._clock.now()
                )
                if failed.state is not OperationState.FAILED or failed.error != payload:
                    return failed, False
                self._journal.append(
                    project_id=failed.project_id,
                    operation_id=failed.operation_id,
                    event_type="operation.failed",
                    payload={"code": error.code, "message": error.message},
                )
        return failed, True

    def _seal_if_gone(
        self, listed: OperationRecord, *, inside_request: bool
    ) -> OwnerVerdict | Literal["SEALED", "ERROR"]:
        """Read the row again (a listing carries no owner), then seal it only if confirmed gone."""
        try:
            operation = self._operations.read(listed.operation_id)
            if operation is None or operation.state is not OperationState.RUNNING:
                return "UNPROVEN"
            verdict = self.verdict(operation)
            if verdict != "GONE":
                return verdict
            _, changed = self._fail_gone(operation, inside_request=inside_request)
            return "SEALED" if changed else "UNPROVEN"
        except Exception:  # one unreadable or refused row must not stop the others
            _LOG.warning("operation %s could not be checked", listed.operation_id, exc_info=True)
            return "ERROR"

    def seal_project(self, project_id: str) -> int:
        """Seal this project's RUNNING operations of any kind whose owner is confirmed gone.

        Used only just before a permanent delete, which removes the project's records anyway.
        Nothing seals at start: an operation whose outcome is unknown (a restore, a source
        connection) stays RUNNING, and a measured command is sealed only when its own key is sent
        again (see seal). An operation with a living or unprovable owner is left for the
        delete's own check.
        """
        try:
            listing = self._operations.list_by_project(project_id)
        except Exception:  # the delete's own check still decides; this pass is only a help
            _LOG.warning("operations of %s could not be listed", project_id, exc_info=True)
            return 0
        sealed = 0
        for listed in listing:
            if listed.state is OperationState.RUNNING:
                sealed += self._seal_if_gone(listed, inside_request=True) == "SEALED"
        return sealed
