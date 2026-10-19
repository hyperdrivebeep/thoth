"""Connect journal event preparation to the operation store's atomic claim boundary."""

from functools import partial

from thoth.domain.operation import OperationRecord
from thoth.ports.journal import JournalPort
from thoth.ports.operation import OperationStorePort
from thoth.protocol.jsonrpc import JsonRpcError, JsonRpcResponse, RpcErrorCode


def claim_operation(
    operations: OperationStorePort, candidate: OperationRecord, journal: JournalPort | None
) -> tuple[OperationRecord, bool]:
    if journal is None:
        return operations.claim(candidate)
    return operations.claim(candidate, started_event=partial(journal.prepare_started, candidate))


def claim_busy_response(request_id: str | int) -> JsonRpcResponse:
    return JsonRpcResponse(
        id=request_id,
        error=JsonRpcError(
            code=RpcErrorCode.DOMAIN_REJECTED,
            message="operation storage is busy; retry the same request later",
            data={
                "reason_code": "OPERATION_CLAIM_BUSY",
                "operation_claimed": False,
                "retryable": True,
            },
        ),
    )
