"""LOCAL recovery for measured commands whose original worker is confirmed gone."""

from collections.abc import Callable
from contextlib import AbstractContextManager

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

    def gone(self, operation: OperationRecord, method: str) -> bool:
        owner = operation.owner
        if (
            operation.state is not OperationState.RUNNING
            or method != operation.method
            or method not in SEALABLE_METHODS
            or owner is None
            or self._record is None
            or owner.host_token is None
            or owner.host_token != self._host_token
            or owner.process_marker == "UNKNOWN"
            or not self._holds_workspace()
        ):
            return False
        return (
            self._worker.is_alive(owner.process_id, owner.process_marker, owner.worker_id) is False
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
        with self._pin_workspace() as held:
            if not held:
                return None
            with resource_use_scope(operation.project_id), self._uow.transaction():
                current = self._operations.read(operation.operation_id)
                if current is None:
                    return None
                if self._resource_access is not None:
                    self._resource_access.require_operation(current)
                if current.state is not OperationState.RUNNING:
                    return current
                if current.owner != operation.owner:
                    return current
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
                    return failed
                self._journal.append(
                    project_id=failed.project_id,
                    operation_id=failed.operation_id,
                    event_type="operation.failed",
                    payload={"code": error.code, "message": error.message},
                )
        measure(request, failed.project_id, "FAILED")
        return failed
